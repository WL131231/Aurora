# Aurora 개발용 시작 스크립트 (Windows PowerShell).
#
# 두 가지 모드:
#   .\scripts\dev.ps1            → Pywebview + FastAPI 통합 (기본)
#   .\scripts\dev.ps1 -ApiOnly   → FastAPI 만 (브라우저로 ui/index.html 직접 열기)
#
# 사전 조건:
#   1. venv 활성화: .\venv\Scripts\Activate.ps1
#   2. pip install -r requirements-dev.txt
#   3. pip install -e .
#
# 담당: 정용우

param(
    [switch]$ApiOnly
)

$ErrorActionPreference = "Stop"

# venv 활성화 확인
if (-not $env:VIRTUAL_ENV) {
    Write-Host "⚠️  venv 가 활성화되지 않았습니다." -ForegroundColor Yellow
    Write-Host "   먼저 실행: .\venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    exit 1
}

# 프로젝트 루트로 이동
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if ($ApiOnly) {
    Write-Host "▶ FastAPI 서버 단독 시작 (http://127.0.0.1:8765)" -ForegroundColor Cyan
    Write-Host "  ui/index.html 을 브라우저에서 직접 열어 확인" -ForegroundColor DarkGray
    python -c "import uvicorn; from aurora.interfaces.api import create_app; uvicorn.run(create_app(), host='127.0.0.1', port=8765, log_level='info')"
} else {
    Write-Host "▶ Pywebview + FastAPI 통합 시작" -ForegroundColor Cyan
    python -m aurora.interfaces.webview
}
