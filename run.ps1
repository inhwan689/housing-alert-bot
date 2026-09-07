# 봇 1회 실행. 작업 스케줄러가 호출하는 진입점이기도 하다.
#   .\run.ps1            실제 알림 전송
#   .\run.ps1 -DryRun    콘솔 출력만
#   .\run.ps1 -Seed      현재 공고를 '이미 본 것'으로 저장 (최초 1회)
param(
    [switch]$DryRun,
    [switch]$Seed,
    [switch]$Detail,
    [string]$Source = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "가상환경이 없습니다. 먼저: python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt"
    exit 1
}

# 한글 로그가 깨지지 않도록
$env:PYTHONIOENCODING = "utf-8"

$argv = @("-m", "src.main")
if ($DryRun) { $argv += "--dry-run" }
if ($Seed)   { $argv += "--seed" }
if ($Detail) { $argv += "--verbose" }
if ($Source) { $argv += @("--source", $Source) }

& $python @argv
exit $LASTEXITCODE
