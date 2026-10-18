# 编译 moe_bullet_bake.exe 到 work/moe_bullet_build/。Bullet 静态库一次编好后增量很快。
$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..\..')
$vc = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat'
$bld = Join-Path $root 'work\moe_bullet_build'
New-Item -ItemType Directory -Force $bld | Out-Null
$src = $PSScriptRoot
# 2026-09-06 tools/ 按管线阶段重组后源目录从 tools\moe_bullet 搬到 tools\physics\moe_bullet，
# 旧缓存记着旧路径，cmake 直接报「source does not match」拒绝配置。缓存的源目录与当前不一致就删掉重配
# （Bullet 静态库要重编一次，几分钟）。
$cache = Join-Path $bld 'CMakeCache.txt'
if (Test-Path $cache) {
    $home_ = (Select-String -Path $cache -Pattern '^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$').Matches[0].Groups[1].Value
    if ($home_ -and ((Resolve-Path $home_ -ErrorAction SilentlyContinue).Path -ne (Resolve-Path $src).Path)) {
        Write-Host "cmake 缓存源目录 $home_ != $src，删缓存重配"
        Remove-Item $cache -Force
        Remove-Item (Join-Path $bld 'CMakeFiles') -Recurse -Force -ErrorAction SilentlyContinue
    }
}
cmd /c "`"$vc`" >nul 2>&1 && cmake -S `"$src`" -B `"$bld`" -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build `"$bld`""
if ($LASTEXITCODE -ne 0) { throw "build failed ($LASTEXITCODE)" }
Get-Item (Join-Path $bld 'moe_bullet_bake.exe') | Select-Object FullName, Length, LastWriteTime
