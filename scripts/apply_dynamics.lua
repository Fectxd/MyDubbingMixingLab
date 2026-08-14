-- apply_dynamics.lua
-- 在 REAPER 里给演员轨搭好「压缩 → 增益补偿 → 限幅」链（不碰源文件）。
--
-- 思路和 master.py 完全一致：
--   1) ReaComp  低阈值压缩，把安静台词也拉近大声台词（Auto makeup 关掉，
--      由第 2 步显式补偿，避免它自己乱加增益）；
--   2) JS Volume Adjustment  用 master_report 算好的增益把整轨响度抬/压到
--      参照轨水平（这就是“把响度拉起来”的那一步，放在限幅器之前）；
--   3) ReaLimit  最后兜住峰值（-0.4 dB 天花板）。
-- 轨道音量（fader）归 0 dB，方便之后手动微调平衡。
--
-- 用法：
--   1) python assemble_rpp.py --actors test
--   2) 在 REAPER 打开 work/reaper/EP05_配音工程.rpp
--   3) Actions（? 键）→ ReaScript: Load… → 选本文件 → Run
-- 脚本可重复运行：已存在的效果器会被复用，只更新参数，不会重复插入。

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

-- 参数按名字前缀匹配（兼容 ReaComp 新旧版参数名），返回是否设置成功。
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

-- 在轨道上找已有的效果器；没有才新增（保证重复运行幂等）。
local function ensure_fx(tr, names)
  for _, n in ipairs(names) do
    local fx = reaper.TrackFX_GetByName(tr, n, false)
    if fx >= 0 then return fx end
  end
  for _, n in ipairs(names) do
    local fx = reaper.TrackFX_AddByName(tr, n, false, -1)
    if fx >= 0 then return fx end
  end
  return -1
end

reaper.Undo_BeginBlock()
local ntr = reaper.CountTracks(0)
local touched = 0
local failed = {}

for i = 0, ntr - 1 do
  local tr = reaper.GetTrack(0, i)
  local ret, tname = reaper.GetTrackName(tr, "")
  local cfg = dynamics[tname]
  if cfg and type(cfg) == "table" and (cfg.threshold_db ~= nil or cfg.limiter_ceiling_db ~= nil) then
    local fx_comp = ensure_fx(tr, { "ReaComp", "ReaComp (Cockos)" })
    if cfg.threshold_db ~= nil then
      if fx_comp >= 0 then
        if not (set_param(tr, fx_comp, { "threshold", "thresh" }, cfg.threshold_db)
            and set_param(tr, fx_comp, "ratio", cfg.ratio)
            and set_param(tr, fx_comp, "attack", cfg.attack)
            and set_param(tr, fx_comp, "release", cfg.release)) then
          failed[#failed + 1] = tname .. " (ReaComp 参数未完全匹配)"
        end
        -- 关掉自动增益补偿，改用下面显式的 JS 补偿
        set_param(tr, fx_comp, { "automkup", "auto makeup", "auto make-up" }, 0)
      else
        failed[#failed + 1] = tname .. " (ReaComp)"
      end
    end

    if cfg.gain_db ~= nil then
      local fx_gain = ensure_fx(tr, { "JS: Volume Adjustment", "JS:Volume Adjustment", "volume" })
      if fx_gain >= 0 then
        -- Adjustment (dB) 做响度补偿；Max Volume 默认 0 dB 会硬削波，先抬到
        -- +18 dB，真正兜峰值交给后面的 ReaLimit。
        local ok_adj = set_param(tr, fx_gain, "adjustment", cfg.gain_db)
        local ok_max = set_param(tr, fx_gain, "max volume", 18.0)
        if not (ok_adj and ok_max) then
          failed[#failed + 1] = tname .. " (JS 增益参数未匹配)"
        end
      else
        failed[#failed + 1] = tname .. " (JS: Volume Adjustment)"
      end
    end

    if cfg.limiter_ceiling_db ~= nil then
      local fx_lim = ensure_fx(tr, { "ReaLimit", "ReaLimit (Cockos)" })
      if fx_lim >= 0 then
        if not set_param(tr, fx_lim, "threshold", cfg.limiter_ceiling_db) then
          failed[#failed + 1] = tname .. " (ReaLimit 参数未匹配)"
        end
      else
        failed[#failed + 1] = tname .. " (ReaLimit)"
      end
    end

    -- 增益已放进效果器链，轨道音量归 0 dB 方便手调平衡
    if cfg.gain_db ~= nil then
      reaper.SetMediaTrackInfo_Value(tr, "D_VOL", 1.0)
      reaper.SetMediaTrackInfo_Value(tr, "D_PAN", 0)
    end
    touched = touched + 1
  end
end

reaper.Undo_EndBlock("apply dynamics (ReaComp + makeup + ReaLimit)", -1)

local msg = string.format("已处理 %d 条演员轨：压缩 + 增益补偿 + 限幅，轨道音量归 0 dB\n", touched)
if #failed > 0 then
  msg = msg .. "未能完全设置：\n"
  for _, n in ipairs(failed) do
    msg = msg .. "  " .. n .. "\n"
  end
end
reaper.ShowConsoleMsg(msg)
