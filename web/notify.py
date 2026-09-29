"""Windows toast notifications for scheduled-run alerts. This is a local single-user tool (CLAUDE.md
Sec.0.1: dev machine is Windows), so the notification goes to the same desktop the server runs on.

Uses the WinRT toast API through PowerShell 5.1 - no extra package or module. The title/body reach the
script through environment variables and are XML-escaped there, never interpolated into the script text,
so a banner's alt text can't break the toast XML or inject commands. Never raises: a notification that
can't be shown must not fail the run's own bookkeeping - the caller records the returned string instead.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys

# PowerShell's own AppUserModelID - a toast has to be attributed to a registered app to be shown, and
# this one exists on every Windows 10/11 install.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$title = [System.Security.SecurityElement]::Escape($env:AFV_TOAST_TITLE)
$body = [System.Security.SecurityElement]::Escape($env:AFV_TOAST_BODY)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$body</text></binding></visual></toast>")
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('""" + _APP_ID + r"""').Show($toast)
"""

_TIMEOUT_S = 20


def toast(title: str, body: str) -> str:
    """Show a toast. Returns "ok", or "skipped: ..." / "error: ..." describing why it wasn't shown."""
    if sys.platform != "win32":
        return "skipped: Windows notifications are only available on Windows"
    encoded = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
    env = {**os.environ, "AFV_TOAST_TITLE": title[:120], "AFV_TOAST_BODY": body[:400]}
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                              env=env, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return "error: " + " ".join((proc.stderr or proc.stdout or f"exit code {proc.returncode}").split())[:200]
    return "ok"
