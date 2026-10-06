# build.ps1 -- one-shot build of the necopn_player YM2608 core DLL.
#
# Produces:  necopn_player/core/necopna_ymfm.dll
# Toolchain: the PyPI "ziglang" package used as a C++ compiler
#            (python -m ziglang c++  ==  clang 21, target x86_64-windows-gnu).
#            This machine has no gcc/clang/msvc; zig is the only C++ toolchain.
#
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = 'Stop'
$core = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $core

# 1) make sure the ziglang wheel is installed
python -c "import ziglang" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "installing ziglang from PyPI ..."
    python -m pip install ziglang
    if ($LASTEXITCODE -ne 0) { throw "pip install ziglang failed" }
}

# 2) compile + link
$sources = @(
    'necopna_ymfm.cpp',
    'vendor/ymfm/src/ymfm_opn.cpp',
    'vendor/ymfm/src/ymfm_adpcm.cpp',
    'vendor/ymfm/src/ymfm_ssg.cpp'
)
$log = Join-Path $env:TEMP 'necopna_build.log'
Write-Host "zig c++ -O2 -shared -> necopna_ymfm.dll"
& python -m ziglang c++ -std=c++14 -O2 -shared -DNOPNA_BUILD -I . @sources -o necopna_ymfm.dll 2> $log
if ($LASTEXITCODE -ne 0) {
    Get-Content $log | Select-Object -Last 40
    throw "build failed (full log: $log)"
}

$dll = Join-Path $core 'necopna_ymfm.dll'
if (-not (Test-Path $dll)) { throw "build reported success but $dll is missing" }
Write-Host ("built {0}  ({1} bytes)" -f $dll, (Get-Item $dll).Length)
