<#
  Tests the Windows autostart (scripts\autostart.ps1) and that the scheduler comes back with it.

    powershell -ExecutionPolicy Bypass -File scripts\test_autostart.ps1               # safe: reads only, touches nothing
    powershell -ExecutionPolicy Bypass -File scripts\test_autostart.ps1 -Restart      # stops the server, starts it through the task
    powershell -ExecutionPolicy Bypass -File scripts\test_autostart.ps1 -Crash        # also kills it and reports whether anything brings it back (known: nothing does)
    powershell -ExecutionPolicy Bypass -File scripts\test_autostart.ps1 -AfterLogon   # run right after signing back in: did the logon start it?

  -Restart and -Crash refuse to run while a check is running (a restart would drop the runs queued behind it), and always leave
  the server running at the end. Nothing here can prove the logon trigger fires: only -AfterLogon, after a real sign-out, does.
#>
param([switch]$Restart, [switch]$Crash, [switch]$AfterLogon)

$name = "AJIO Feed Verify server"
$root = Split-Path -Parent $PSScriptRoot
$base = "http://127.0.0.1:8000"
$log = Join-Path $root "logs\server.log"
$script:fails = 0

function Check($ok, $what) { if ($ok) { "PASS  $what" } else { "FAIL  $what"; $script:fails++ } }
function Listener { Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 }
function Api($path) { Invoke-RestMethod "$base$path" -TimeoutSec 5 }
function WaitFor([scriptblock]$cond, $secs) {
    $end = (Get-Date).AddSeconds($secs)
    while ((Get-Date) -lt $end) { try { if (& $cond) { return $true } } catch {}; Start-Sleep 2 }
    $false
}
function StartLines { if (Test-Path $log) { @(Select-String -Path $log -Pattern "=== server starting").Count } else { 0 } }
function Schedules { @(Api "/api/schedules" | Where-Object { $null -ne $_.id } | Sort-Object id | ForEach-Object { "{0}|{1}|{2}" -f $_.id, [bool]$_.enabled, [bool]$_.next_run_at }) }
function Stop-Server {
    $l = Listener
    if ($l) { Stop-Process -Id $l.OwningProcess -Force }
    # the task keeps "Running" until its launcher exits; a new start is ignored while it does
    WaitFor { -not (Listener) -and (Get-ScheduledTask -TaskName $name).State -ne "Running" } 30 | Out-Null
}
function Start-Server { Start-ScheduledTask -TaskName $name; WaitFor { (Api "/api/meta") -ne $null } 90 }
function Refuse-IfRunning {
    $running = @(Api "/api/runs?limit=200" | Where-Object { $_.status -eq "running" })
    if ($running.Count) { "REFUSED: $($running.Count) check(s) are running. Wait for them to finish, or stop them, then run this again."; exit 2 }
}

# ---- after a real sign-out / sign-in ----
if ($AfterLogon) {
    $info = Get-ScheduledTaskInfo -TaskName $name
    $logon = (Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | Sort-Object CreationDate | Select-Object -First 1).CreationDate
    "Signed in (explorer started): $logon"
    "Task last ran:               $($info.LastRunTime)   (result $($info.LastTaskResult); 267009 = still running, which is right)"
    $gap = ($info.LastRunTime - $logon).TotalSeconds
    Check ($gap -ge 0 -and $gap -le 300) ("the task ran {0:N0} s after sign-in (expected about 30 s)" -f $gap)
    Check ([bool](Listener)) "the server is listening on port 8000"
    Check ((WaitFor { (Api "/api/meta") -ne $null } 30)) "the API answers"
    $line = if (Test-Path $log) { Select-String -Path $log -Pattern "=== server starting" | Select-Object -Last 1 } else { $null }
    "Last start in logs\server.log: $($line.Line)"
    $s = Schedules; "Schedules in the database: $($s.Count); enabled ones with a next run: $(@($s | Where-Object { $_ -like '*|True|True' }).Count)"
    Check (@($s | Where-Object { $_ -like '*|True|False' }).Count -eq 0) "every enabled schedule has a next run (the scheduler was rebuilt)"
    if ($script:fails) { "$script:fails check(s) FAILED"; exit 1 } else { "All good: the logon started the server and its schedules."; exit 0 }
}

# ---- 1. the task is set up the way autostart.ps1 promises ----
$task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
Check ($null -ne $task) "the task '$name' is installed (run scripts\autostart.ps1 if not)"
if (-not $task) { exit 1 }
$trig = $task.Triggers | Select-Object -First 1
$act = $task.Actions | Select-Object -First 1
Check ($task.Settings.Enabled -and $trig.Enabled) "the task and its trigger are enabled"
Check ($trig.CimClass.CimClassName -eq "MSFT_TaskLogonTrigger") "it starts at logon"
Check ($trig.Delay -eq "PT30S") "30 s after logon"
Check ($trig.UserId -ieq "$env:USERDOMAIN\$env:USERNAME") "for this user only"
Check ($task.Principal.RunLevel -eq "Limited") "without admin rights"
$launcher = if ($act.Arguments -match '-File "([^"]+)"') { $Matches[1] } else { "" }
Check ($launcher -and (Test-Path $launcher)) "the launcher it runs exists: $launcher"
Check ($launcher -ieq (Join-Path $root "scripts\start_server.ps1")) "and it is this repo's (not a moved or old copy)"
Check (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) "the Python the launcher needs exists (.venv)"

# ---- 2. the launcher is safe to run twice ----
$l = Listener
if ($l) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher
    Check ($LASTEXITCODE -eq 0 -and (Listener).OwningProcess -eq $l.OwningProcess) "running the launcher again starts no second server"
} else { "SKIP  the server is not running, so the 'no second copy' check was skipped" }

# ---- 3. the scheduler's schedules are in the API ----
$before = $null
if (Listener) {
    $before = Schedules
    "INFO  $($before.Count) schedule(s) now; enabled: $(@($before | Where-Object { $_ -like '*|True|*' }).Count)"
    Check (@($before | Where-Object { $_ -like '*|True|False' }).Count -eq 0) "every enabled schedule has a next run"
}

# ---- 4. a start by the task (what the logon does), and the schedules after it ----
if ($Restart -or $Crash) {
    if (-not $before) { "The server must be running to start this test."; exit 2 }
    Refuse-IfRunning
    try {
        $lines = StartLines
        Stop-Server
        Check (-not (Listener)) "the server is stopped"
        Check (Start-Server) "starting the task brings the server back (within 90 s)"
        Check ((StartLines) -eq $lines + 1) "the launcher logged one new start in logs\server.log"
        $after = Schedules
        Check (($before -join ",") -eq ($after -join ",")) "the same schedules, enabled and with a next run, after the restart"

        if ($Crash) {
            $pid1 = (Listener).OwningProcess
            Stop-Process -Id $pid1 -Force
            "INFO  killed the server (simulated crash); waiting 90 s to see whether anything restarts it"
            $back = WaitFor { (Listener) -and (Listener).OwningProcess -ne $pid1 } 90
            # Known and accepted: Task Scheduler's restart-on-failure only covers a task that fails to launch, not a server that dies later.
            $msg = if ($back) { "WAS restarted by itself" } else { "stays down until the next logon, or Start-ScheduledTask" }
            "INFO  after a crash the server $msg"
        }
    } finally {
        if (-not (Listener)) { "INFO  starting the server again"; Start-Server | Out-Null }
        "INFO  the server is $(if (Listener) { 'running' } else { 'NOT running' })"
    }
}

if ($script:fails) { "$script:fails check(s) FAILED"; exit 1 }
"All checks passed." + $(if (-not ($Restart -or $Crash)) { " (-Restart and -AfterLogon go further.)" } else { "" })
