param (
    [switch]$RunAfterBuild = $false
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host "Building AntigravityUsageMonitor with PyInstaller..." -ForegroundColor Cyan

Set-Location $ScriptDir

# ビルド実行 (-w, --noconsole を指定してウィンドウを出さないようにする。 --onefile で完全な単一ファイルにする)
python -m PyInstaller --noconfirm --onefile --windowed --name "AntigravityUsageMonitor" --clean .\monitor.py

if ($LASTEXITCODE -eq 0) {
    Write-Host "Build completed successfully." -ForegroundColor Green
    
    # 実行ファイルのパス
    $exePath = Join-Path $ScriptDir "dist\AntigravityUsageMonitor.exe"
    
    if ($RunAfterBuild) {
        Write-Host "Starting application..." -ForegroundColor Yellow
        Start-Process $exePath
    }
} else {
    Write-Host "Build failed with exit code $LASTEXITCODE" -ForegroundColor Red
}
