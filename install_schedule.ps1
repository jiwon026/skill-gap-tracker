# Skill Gap Tracker 일일 실행을 Windows 작업 스케줄러에 등록한다.
#
# 시간을 바꾸려면 -At 인자만 주면 된다:
#     powershell -ExecutionPolicy Bypass -File install_schedule.ps1 -At 07:30
#
# 관리자 권한이 필요 없다. 현재 사용자 계정으로 등록되므로 사용자 환경변수
# (NOTION_TOKEN, SARAMIN_ACCESS_KEY)를 그대로 물려받는다. SYSTEM 계정으로
# 등록하면 그 환경변수가 없어 조용히 아무것도 안 하게 된다.

param(
    [string]$At = '08:00',
    [string]$TaskName = 'JobGap 일일 수집'
)

$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$script = Join-Path $root 'run_daily.ps1'

if (-not (Test-Path $script)) {
    throw "run_daily.ps1 을 찾을 수 없습니다: $script"
}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -Daily -At $At

# StartWhenAvailable  PC가 꺼져 있어 놓친 실행을 켜진 뒤에 따라잡는다.
#                     이게 없으면 그날 실행은 그냥 사라진다.
# 배터리 관련 두 옵션  노트북 기본값은 배터리에서 실행하지 않는 것이다.
# IgnoreNew           앞 실행이 아직 돌고 있으면 새로 띄우지 않는다.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description '데이터 분석 채용공고 수집 → 직군·경력 필터 → 스킬 갭·경험 매칭 → Notion 적재' `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host ''
Write-Host "등록됨: $TaskName"
Write-Host ("  상태      : " + $task.State)
Write-Host ("  실행 계정 : " + $task.Principal.UserId)
Write-Host ("  실행 시각 : 매일 " + $At)
Write-Host ("  다음 실행 : " + $info.NextRunTime)
Write-Host ("  스크립트  : " + $script)
