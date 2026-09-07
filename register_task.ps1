# Windows 작업 스케줄러에 매일 실행을 등록한다.
# 사용: powershell -ExecutionPolicy Bypass -File .\register_task.ps1
# 해제: Unregister-ScheduledTask -TaskName "HousingAlertBot" -Confirm:$false
param(
    [string]$TaskName = "HousingAlertBot",
    [string]$At = "09:10"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$runner = Join-Path $root "run.ps1"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`"" `
    -WorkingDirectory $root

# 하루 두 번(오전/오후). 공고는 실시간성이 필요 없고, 과도한 호출은 차단 위험만 키운다.
$trigger1 = New-ScheduledTaskTrigger -Daily -At $At
$trigger2 = New-ScheduledTaskTrigger -Daily -At ([datetime]::Parse($At).AddHours(9))

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($trigger1, $trigger2) -Settings $settings -Force | Out-Null

Write-Output "등록 완료: $TaskName ($At, $([datetime]::Parse($At).AddHours(9).ToString('HH:mm')))"
Write-Output "지금 바로 테스트: Start-ScheduledTask -TaskName $TaskName"
