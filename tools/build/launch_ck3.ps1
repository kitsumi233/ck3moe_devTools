# 用隔离 userdir 启动 CK3。
#
# 为什么必须隔离：常规 userdir 启用了 63 个 mod，其中 CBO 系列本身就替换肖像
# mesh。在那个环境里验证，看到问题也分不清是我们的 mod 还是别的 mod 冲突。
#
# **装 mod 不在这里做** —— `tools/build/finalize_mods.py` 负责把五个子 mod 拷进
# 隔离 userdir 并写 `dlc_load.json`（`build_all.py` 的最后一步）。这个脚本
# 只负责清日志 + 起进程。前身 `setup_florence_isolated.ps1` 会把
# `dlc_load.json` 重写成「只启用单体 mod」，单体退役后那等于拆掉七 mod
# 配置，已于 2026-08-29 进 trash。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File tools\build\launch_ck3.ps1
#   powershell -ExecutionPolicy Bypass -File tools\build\launch_ck3.ps1 -KeepLogs

param(
    [switch]$KeepLogs      # 保留旧日志（默认清掉，保证这次的报错是干净的）
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # tools/build/ → 根
$userDir = Join-Path $projectRoot "work\ck3_florence_userdir"
$exe = 'E:\SteamLibrary\steamapps\common\Crusader Kings III\binaries\ck3.exe'

if (-not (Test-Path -LiteralPath $exe)) { throw "找不到 ck3.exe: $exe" }

$dlc = Join-Path $userDir "dlc_load.json"
if (-not (Test-Path -LiteralPath $dlc)) {
    throw "没有 dlc_load.json，先跑: python tools\build\finalize_mods.py"
}
$mods = (Get-Content -LiteralPath $dlc -Raw | ConvertFrom-Json).enabled_mods
Write-Output "启用的 mod ($($mods.Count) 个):"
$mods | ForEach-Object { Write-Output "  $_" }

# 旧日志里的「0 错误」会被误读成修复成功，所以默认清掉。
$logDir = Join-Path $userDir "logs"
if (-not $KeepLogs -and (Test-Path -LiteralPath $logDir)) {
    Get-ChildItem -LiteralPath $logDir -Filter "*.log" -Force -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Write-Output "已清空 $logDir 下的 *.log"
}

# -debug_mode 必须给：否则不写 error.log，控制台命令也用不了。
$p = Start-Process -FilePath $exe -PassThru -ArgumentList `
    '-gdpr-compliant', '-debug_mode', "-userdir=$userDir"
Write-Output ""
Write-Output "PID $($p.Id)  启动于 $($p.StartTime.ToString('HH:mm:ss'))"
Write-Output "日志: $logDir"
Write-Output "[CK3 LAUNCHED]"
