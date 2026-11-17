# build_exe.ps1
# 打包成单文件 EXE（GUI 版，仿照 OPMMIDI-RT）。
#
#   .\build_exe.ps1              # 窗口版（默认）
#   .\build_exe.ps1 -Console     # 带控制台，排错用
#   .\build_exe.ps1 -Name MyPlayer
#
# 注意：**不会打包任何 RHY/节奏采样**（版权素材）。EXE 只带三个原生 DLL：
#   opn2.dll、ssg.dll、core/necopna_fmgen.dll
param(
    [string]$Name = "NECOPN-RT",
    [switch]$Console
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "PATH 里找不到 python"
}
python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "安装 PyInstaller…" -ForegroundColor Yellow
    python -m pip install -q pyinstaller
}

$mode = if ($Console) { "--console" } else { "--windowed" }

$piArgs = @(
    "--noconfirm", "--clean", "--onefile", $mode,
    "--name", $Name,
    # 三个原生核心
    "--add-binary", "opn2.dll;.",
    "--add-binary", "ssg.dll;.",
    "--add-binary", "core\necopna_fmgen.dll;core",
    # PortAudio 的 DLL 在 sounddevice 包里，不 collect 会漏
    "--collect-data", "sounddevice",
    "app.py"
)

Write-Host "构建 $Name ($mode)…" -ForegroundColor Cyan
python -m PyInstaller @piArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

$exe = Join-Path $PSScriptRoot "dist\$Name.exe"
if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "完成: $exe ($mb MB)" -ForegroundColor Green
    Write-Host "自检: & '$exe' --selfcheck   → 结果写进 dist\selfcheck.log" -ForegroundColor Green
} else {
    throw "没有生成 EXE"
}
