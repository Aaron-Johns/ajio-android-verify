# Starts the AJIO Feed Verify server with no window, logging to logs\server.log. This is what the
# "AJIO Feed Verify server" Windows scheduled task runs at logon (see scripts\autostart.ps1); it can also be
# run by hand. It does nothing if something is already listening on port 8000, so it can never start a
# second copy - and never fights a server you started yourself with run.bat.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { exit 0 }

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Could not find $py"; exit 1 }

$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs "server.log"
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) { Move-Item -Force $log (Join-Path $logs "server.old.log") }
"=== server starting $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Add-Content $log

# cmd does the redirection so uvicorn's stderr (where its log lines go) lands in the file too
cmd.exe /c "`"$py`" -m uvicorn web.api:app --host 127.0.0.1 --port 8000 >> `"$log`" 2>&1"
