-- apply_sidechain.lua
-- 分组侧链：只要任何演员开口，背景音乐/音效就自动“让路”（ducking）。
--
-- 做法（全部在 REAPER 内完成，不碰音频文件）：
--   1) 找到/新建「对白触发」总线轨（不送主输出，本身听不到）；
--   2) 5 条演员轨各送一份到总线（post-FX，跟随压缩/增益后的实际音量）；
--   3) 总线送到 背景-音乐 / 背景-音效 的 3/4 通道（轨道要开 4 通道）；
--   4) 两条背景轨各加一个 ReaComp，探测输入切到 Aux（3/4）：
--      有对白时背景被压低，对白一停自动恢复。
--
-- 用法：REAPER Actions（? 键）→ ReaScript: Load… → 选本文件 → Run。
-- 可重复运行：已有的路由和效果器会被复用，只更新参数。
-- 建议先跑 apply_dynamics.lua 再跑本脚本（或直接跑 apply_mix.lua）。

local BUS_NAME = "对白触发"
local DUCK_THRESHOLD_DB = -28.0  -- 对白超过这个电平就开始压低背景
local DUCK_RATIO = 4.0           -- 4:1，压得自然且明显
local DUCK_ATTACK_MS = 8.0       -- 快速跟上对白开头
local DUCK_RELEASE_MS = 300.0    -- 对白间隙背景较快回来

local script_dir = debug.getinfo(1, "S").source:match("^@(.*)[/\\][^/\\]+$")
if not script_dir then
  reaper.ShowConsoleMsg("无法确定脚本目录，请把脚本放在仓库 scripts/ 下运行\n")
  return
end

-- 读取 assemble_rpp.py 生成的演员名单（同时拿到参数文件名做提示）
local actors = nil
local dyn_path = script_dir .. "/../work/reaper/EP05_dynamics.lua"
local chunk, err = loadfile(dyn_path)
if chunk then
  local ok, dynamics = pcall(chunk)
  if ok and type(dynamics) == "table" and type(dynamics.actors) == "table" then
    actors = dynamics.actors
  end
end

local function track_name(tr)
  local ok, n = reaper.GetTrackName(tr, "")
  if ok then return n end
  return ""
end

local function get_track_by_name(name)
  for i = 0, reaper.CountTracks(0) - 1 do
    local tr = reaper.GetTrack(0, i)
    if track_name(tr) == name then return tr end
  end
  return nil
end

local function find_or_create_bus(name)
  local tr = get_track_by_name(name)
  if tr then return tr, false end
  local idx = reaper.CountTracks(0)
  reaper.InsertTrackAtIndex(idx, false)
  tr = reaper.GetTrack(0, idx)
  reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", name, true)
  reaper.SetMediaTrackInfo_Value(tr, "B_MAINSEND", 0) -- 总线不送主输出
  return tr, true
end

local function ensure_send(src, dst, dstchan)
  local ns = reaper.GetTrackNumSends(src, 0)
  for i = 0, ns - 1 do
    if reaper.GetTrackSendInfo_Value(src, 0, i, "P_DESTTRACK") == dst then
      reaper.SetTrackSendInfo_Value(src, 0, i, "I_DSTCHAN", dstchan)
      return i, false
    end
  end
  local idx = reaper.CreateTrackSend(src, dst)
  if idx >= 0 then
    reaper.SetTrackSendInfo_Value(src, 0, idx, "I_DSTCHAN", dstchan)
  end
  return idx, true
end

local function set_param(tr, fx, prefixes, value)
  if value == nil then return false end
  if type(prefixes) == "string" then prefixes = { prefixes } end
  local n = reaper.TrackFX_GetNumParams(tr, fx)
  for p = 0, n - 1 do
    local ret, name = reaper.TrackFX_GetParamName(tr, fx, p, "")
    if ret and name then
      local low = name:lower()
      for _, prefix in ipairs(prefixes) do
        if low:match("^" .. prefix) then
          local r, minv, maxv = reaper.TrackFX_GetParam(tr, fx, p)
          if maxv > minv then
            local norm = (value - minv) / (maxv - minv)
            if norm < 0 then norm = 0 end
            if norm > 1 then norm = 1 end
            reaper.TrackFX_SetParam(tr, fx, p, norm)
            return true
          end
        end
      end
    end
  end
  return false
end

local function ensure_reacomp(tr)
  local fx = reaper.TrackFX_GetByName(tr, "ReaComp", false)
  if fx < 0 then fx = reaper.TrackFX_AddByName(tr, "ReaComp", false, -1) end
  return fx
end

reaper.Undo_BeginBlock()

local bus, bus_created = find_or_create_bus(BUS_NAME)
local wired = {}

-- 1) 收集演员轨：优先用 dynamics 文件里的名单，否则排除背景/参考/总线
local actor_tracks = {}
local seen = {}
for i = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, i)
  local n = track_name(tr)
  local is_actor = false
  if actors and type(actors) == "table" then
    for _, a in ipairs(actors) do
      if a == n then is_actor = true break end
    end
  else
    is_actor = n ~= "" and n ~= BUS_NAME
      and not n:match("^背景%-")
      and not n:match("^参考%-")
  end
  if is_actor and not seen[n] then
    seen[n] = true
    actor_tracks[#actor_tracks + 1] = tr
  end
end

-- 2) 演员轨 → 总线（1/2）
for _, tr in ipairs(actor_tracks) do
  local idx = ensure_send(tr, bus, 0)
  if idx >= 0 then wired[#wired + 1] = track_name(tr) end
end

-- 3) 总线 → 背景轨 3/4，背景轨开 4 通道 + 加 ducking ReaComp
local ducked = {}
for i = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, i)
  local n = track_name(tr)
  if n:match("^背景%-") then
    reaper.SetMediaTrackInfo_Value(tr, "I_NCHAN", 4)
    if ensure_send(bus, tr, 2) >= 0 then
      local fx = ensure_reacomp(tr)
      if fx >= 0 then
        local ok_sign = set_param(tr, fx, "signin", 1)         -- 探测 Aux 3/4
        set_param(tr, fx, { "automkup", "auto makeup", "auto make-up" }, 0)
        local ok_all =
          ok_sign
          and set_param(tr, fx, { "threshold", "thresh" }, DUCK_THRESHOLD_DB)
          and set_param(tr, fx, "ratio", DUCK_RATIO)
          and set_param(tr, fx, "attack", DUCK_ATTACK_MS)
          and set_param(tr, fx, "release", DUCK_RELEASE_MS)
        ducked[#ducked + 1] = n .. (ok_all and "" or "（参数未完全匹配，请手动核对）")
      else
        ducked[#ducked + 1] = n .. "（加 ReaComp 失败）"
      end
    end
  end
end

reaper.Undo_EndBlock("apply sidechain ducking (对白触发)", -1)

local msg = string.format(
  "侧链完成：%d 条演员轨 →「%s」→ 背景轨 3/4（%d 条背景轨已加 ducking ReaComp）\n",
  #wired, BUS_NAME, #ducked
)
if bus_created then
  msg = msg .. "（总线轨为新建，已关闭主输出）\n"
end
for _, n in ipairs(ducked) do
  msg = msg .. "  " .. n .. "\n"
end
msg = msg
  .. string.format("ducking 参数：阈值 %.0f dB，比例 %.0f:1，attack %.0fms，release %.0fms\n",
                   DUCK_THRESHOLD_DB, DUCK_RATIO, DUCK_ATTACK_MS, DUCK_RELEASE_MS)
  .. "想改强度：选中背景轨 → 打开 ReaComp 直接拧阈值/比例即可。\n"
reaper.ShowConsoleMsg(msg)
