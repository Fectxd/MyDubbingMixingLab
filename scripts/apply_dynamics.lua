-- apply_dynamics.lua
-- 在 REAPER 里给演员轨加 ReaComp + ReaLimit（完全在 DAW 内做，不碰源文件）。
--
-- 用法：
--   1) python assemble_rpp.py --actors test   （默认 raw 非破坏模式，
--      会生成 work/reaper/EP05_dynamics.lua）
--   2) 在 REAPER 里打开 work/reaper/EP05_配音工程.rpp
--   3) Actions（默认 ? 键）→ ReaScript: Load… → 选本文件 → Run
--
-- 脚本按轨道名读取 EP05_dynamics.lua 里记录的压缩/限制参数并逐个设置；
-- 只给有 threshold 记录的轨道加 ReaComp，其余轨道只加 ReaLimit。
-- 重复运行会再插一份效果器：想重来就 Ctrl+Z，或删掉轨道上的 FX。

local script_dir = debug.getinfo(1, "S").source:match("^@(.*)[/\\][^/\\]+$")
if not script_dir then
  reaper.ShowConsoleMsg("无法确定脚本目录，请把脚本放在仓库 scripts/ 下运行\n")
  return
end

local dyn_path = script_dir .. "/../work/reaper/EP05_dynamics.lua"
local chunk, err = loadfile(dyn_path)
if not chunk then
  reaper.ShowConsoleMsg(
    "找不到/无法解析 " .. dyn_path .. "\n"
      .. "请先运行 assemble_rpp.py 生成工程（会同时写出 dynamics 文件）\n"
      .. (err or "") .. "\n"
  )
  return
end

local ok, dynamics = pcall(chunk)
if not ok or type(dynamics) ~= "table" then
  reaper.ShowConsoleMsg("dynamics 文件内容不是有效表格\n")
  return
end

local function set_param(tr, fx, prefix, value)
  if value == nil then return false end
  local n = reaper.TrackFX_GetNumParams(tr, fx)
  for p = 0, n - 1 do
    local ret, name = reaper.TrackFX_GetParamName(tr, fx, p, "")
    if ret and name and name:lower():match("^" .. prefix) then
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
  return false
end

local function add_fx(tr, names)
  for _, n in ipairs(names) do
    local fx = reaper.TrackFX_AddByName(tr, n, false, -1)
    if fx >= 0 then return fx end
  end
  return -1
end

reaper.Undo_BeginBlock()
local ntr = reaper.CountTracks(0)
local touched = 0
local unmatched = {}
local failed = {}

for i = 0, ntr - 1 do
  local tr = reaper.GetTrack(0, i)
  local ret, tname = reaper.GetTrackName(tr, "")
  local cfg = dynamics[tname]
  if cfg then
    if cfg.threshold_db then
      local fx = add_fx(tr, {"ReaComp", "ReaComp (Cockos)"})
      if fx >= 0 then
        local ok_t = set_param(tr, fx, "threshold", cfg.threshold_db)
        local ok_r = set_param(tr, fx, "ratio", cfg.ratio)
        local ok_a = set_param(tr, fx, "attack", cfg.attack)
        local ok_l = set_param(tr, fx, "release", cfg.release)
        if not (ok_t and ok_r and ok_a and ok_l) then
          failed[#failed + 1] = tname .. " (ReaComp 参数未完全匹配)"
        end
      else
        failed[#failed + 1] = tname .. " (ReaComp)"
      end
    end
    if cfg.limiter_ceiling_db then
      local fx = add_fx(tr, {"ReaLimit", "ReaLimit (Cockos)"})
      if fx >= 0 then
        if not set_param(tr, fx, "threshold", cfg.limiter_ceiling_db) then
          failed[#failed + 1] = tname .. " (ReaLimit 参数未匹配)"
        end
      else
        failed[#failed + 1] = tname .. " (ReaLimit)"
      end
    end
    touched = touched + 1
  else
    unmatched[#unmatched + 1] = tname
  end
end

reaper.Undo_EndBlock("apply dynamics (ReaComp + ReaLimit)", -1)

local msg = string.format("已处理 %d 条演员轨（ReaComp + ReaLimit）\n", touched)
if #unmatched > 0 then
  msg = msg .. "未匹配到参数的轨道：\n"
  for _, n in ipairs(unmatched) do
    msg = msg .. "  " .. n .. "\n"
  end
end
if #failed > 0 then
  msg = msg .. "未能添加效果器：\n"
  for _, n in ipairs(failed) do
    msg = msg .. "  " .. n .. "\n"
  end
end
reaper.ShowConsoleMsg(msg)
