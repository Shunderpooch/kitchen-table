# Register the background tasks for the scan inbox and the review dashboard
# (current user, no admin needed). Run again after moving the repo or venv.
#   Expenses scan inbox  - every 2 minutes: new scans in OneDrive\Scans\Inbox -> review batches
#   Expenses app server  - at sign-in: the app on http://<this-pc>:3000 for the home network
#   Expenses fetch transactions - daily: new card and bank transactions (after `fetch connect`)
param([string]$Python = $env:PYTHON_BIN)
$ErrorActionPreference = "Stop"
$repo = Join-Path $PSScriptRoot "..\.." | Resolve-Path
if (-not $Python) { throw "Pass -Python or set PYTHON_BIN to the backend venv's python.exe" }
$pythonw = Join-Path (Split-Path $Python) "pythonw.exe"
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

$inbox = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$repo\scripts\windows\inbox_task.pyw`"" -WorkingDirectory "$repo\backend"
$every2min = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2)
$inboxSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -Priority 7
Register-ScheduledTask -TaskName "Expenses scan inbox" -Action $inbox -Trigger $every2min -Principal $principal `
    -Settings $inboxSettings -Description "Turns scans saved to OneDrive\Scans\Inbox into review batches (expense tracker)." -Force | Out-Null

$server = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$repo\scripts\windows\start_server.ps1`""
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$serverSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -Priority 7
Register-ScheduledTask -TaskName "Expenses app server" -Action $server -Trigger $atLogon -Principal $principal `
    -Settings $serverSettings -Description "Serves the expenses app on the home network (expense tracker)." -Force | Out-Null

# Daily, at a random minute (the Bridge asks clients to avoid the top of the hour);
# does nothing until `fetch connect` has been run.
$fetch = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$repo\scripts\windows\fetch_task.pyw`"" -WorkingDirectory "$repo\backend"
$daily = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(6).AddMinutes((Get-Random -Minimum 3 -Maximum 57)))
$fetchSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -Priority 7
Register-ScheduledTask -TaskName "Expenses fetch transactions" -Action $fetch -Trigger $daily -Principal $principal `
    -Settings $fetchSettings -Description "Fetches card and bank transactions from SimpleFIN Bridge (expense tracker)." -Force | Out-Null

Write-Host "Registered 'Expenses scan inbox' (every 2 minutes), 'Expenses fetch transactions' (daily) and 'Expenses app server' (at sign-in)."
Write-Host "Start the server now with: Start-ScheduledTask 'Expenses app server'"
