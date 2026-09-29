<#
  Makes the AJIO Feed Verify server start by itself when you log in to Windows, so scheduled runs keep
  happening without you opening anything.

    powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1            # install (or update)
    powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Status    # is it installed / running?
    powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove    # uninstall

  It registers one per-user scheduled task ("AJIO Feed Verify server"): 30 s after you log in it runs
  scripts\start_server.ps1 in a hidden window. It runs as you, only while you're logged in (the Windows
  notifications need your desktop), needs no admin rights, and restarts the server if it crashes.
  A time that comes and goes while the PC is off is simply skipped - the server rebuilds every schedule
  from the database on start and waits for each one's next slot.
#>
param([switch]$Remove, [switch]$Status)

$name = "AJIO Feed Verify server"
$launcher = Join-Path $PSScriptRoot "start_server.ps1"

if ($Status) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) { "Installed: $($task.State)" } else { "Not installed." }
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { "Server: listening on http://127.0.0.1:8000" } else { "Server: not running." }
    exit 0
}

if ($Remove) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        "Removed the '$name' task. (A server that's already running is left running.)"
    } else { "Nothing to remove." }
    exit 0
}

$me = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
$trigger.Delay = "PT30S"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Starts the AJIO Feed Verify web server (scheduled QA runs) when you log in." -Force | Out-Null
"Installed the '$name' task: the server will start 30 s after you log in. Log: $(Join-Path (Split-Path -Parent $PSScriptRoot) 'logs\server.log')"
