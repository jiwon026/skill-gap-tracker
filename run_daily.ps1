# Skill Gap Tracker 일일 실행 래퍼. 작업 스케줄러가 이 파일을 부른다.
#
# 스케줄러가 직접 python.exe 를 부르지 않고 래퍼를 두는 이유는 세 가지다.
#   - 실패했을 때 볼 수 있는 로그가 남아야 한다. 스케줄러는 종료 코드만 알려준다.
#   - Windows 콘솔 기본 코덱(cp949)이 한글·기호에서 터진다.
#   - 작업 디렉터리가 스케줄러 기본값(system32)이면 상대 경로가 전부 깨진다.
#
# 파이썬 출력을 PowerShell 파이프라인에 태우지 않는다. 두 가지가 깨진다 —
# `$ErrorActionPreference='Stop'` 아래에서 네이티브 명령의 stderr 한 줄이
# 종료 오류가 되어 스크립트가 중단되고, PowerShell 5.1 이 출력 바이트를
# 콘솔 코덱으로 디코딩해 한글이 뭉개진다. cmd 리다이렉션으로 파이썬이
# 파일에 직접 쓰게 하면 둘 다 발생하지 않는다.

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

$logDir = Join-Path $root 'store\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stamp   = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$logFile = Join-Path $logDir "$stamp.log"
$utf8    = New-Object System.Text.UTF8Encoding($false)   # BOM 없이

function Write-Log([string]$line) {
    [System.IO.File]::AppendAllText($logFile, "$line`r`n", $utf8)
}

$env:PYTHONIOENCODING = 'utf-8'

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Log "[$stamp] .venv 가 없습니다: $python"
    exit 1
}

Write-Log "[$stamp] Skill Gap Tracker 시작"

# stdout·stderr 를 함께 남긴다. 사람인 키 누락 같은 경고가 stderr 로 나가는데,
# 그것만 놓치면 왜 결과가 비었는지 알 수 없다.
& cmd.exe /c "`"$python`" run.py >> `"$logFile`" 2>&1"
$code = $LASTEXITCODE

Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 종료 코드 $code"

# 로그가 무한히 쌓이지 않게 30일치만 남긴다.
Get-ChildItem -Path $logDir -Filter '*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $code
