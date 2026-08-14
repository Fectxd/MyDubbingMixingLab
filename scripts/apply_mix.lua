-- apply_mix.lua
-- 一键完成整套混音设置：先 apply_dynamics.lua（压缩+增益补偿+限幅），
-- 再 apply_sidechain.lua（对白触发背景 ducking）。
--
-- 用法：REAPER Actions（? 键）→ ReaScript: Load… → 选本文件 → Run。
-- 可重复运行（两个子脚本都是幂等的）。

local script_dir = debug.getinfo(1, "S").source:match("^@(.*)[/\\][^/\\]+$")
if not script_dir then
  reaper.ShowConsoleMsg("无法确定脚本目录，请把脚本放在仓库 scripts/ 下运行\n")
  return
end

reaper.ShowConsoleMsg("正在应用压缩/增益/限幅…\n")
dofile(script_dir .. "/apply_dynamics.lua")
reaper.ShowConsoleMsg("正在应用对白侧链 ducking…\n")
dofile(script_dir .. "/apply_sidechain.lua")
