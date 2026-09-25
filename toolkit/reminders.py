"""reminder — a one-off desktop notification at a set date and time.

Registered with the operating system's own scheduler, so it fires even if
Omni-OS isn't running then. Nothing here depends on a Python interpreter
at fire time (the installed app has none on PATH):

  Windows  a Task Scheduler task that runs a tiny PowerShell script which
           shows a toast notification, then unregisters itself.
  macOS    a launchd agent that runs osascript's `display notification`.
  Linux    a transient systemd user timer running notify-send, or `at`.
"""

from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from core.app_paths import get_data_dir
from toolkit.base import ToolContext, register, S

_TITLE = "Omni-OS reminder"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _reminder_dir() -> Path:
    d = get_data_dir() / "reminders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ps_quote(text: str) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + text.replace("'", "''") + "'"


def _via_task_scheduler(when: datetime, task: str, message: str) -> None:
    script = _reminder_dir() / f"{task}.ps1"
    script.write_text(
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
        "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
        "$lines = $xml.GetElementsByTagName('text')\n"
        f"$lines[0].AppendChild($xml.CreateTextNode({_ps_quote(_TITLE)})) | Out-Null\n"
        f"$lines[1].AppendChild($xml.CreateTextNode({_ps_quote(message)})) | Out-Null\n"
        "$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($xml))\n"
        "[console]::beep(880, 200); [console]::beep(1100, 200)\n"
        f"Unregister-ScheduledTask -TaskName {_ps_quote(task)} -Confirm:$false\n"
        "Remove-Item -LiteralPath $PSCommandPath\n",
        encoding="utf-8-sig",
    )
    register_cmd = (
        "$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "
        + _ps_quote(f'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{script}"') + "\n"
        f"$trigger = New-ScheduledTaskTrigger -Once -At ([datetime]::Parse({_ps_quote(when.isoformat())}))\n"
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries\n"
        f"Register-ScheduledTask -TaskName {_ps_quote(task)} -Action $action -Trigger $trigger "
        "-Settings $settings -Force | Out-Null\n"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-Command", register_cmd],
                          capture_output=True, text=True, creationflags=_NO_WINDOW)
    if done.returncode != 0:
        script.unlink(missing_ok=True)
        raise RuntimeError((done.stderr or done.stdout).strip() or "Task Scheduler refused the task")


def _via_launchd(when: datetime, task: str, message: str) -> None:
    label = f"io.kondux.omnios.{task}"
    agent = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    agent.parent.mkdir(parents=True, exist_ok=True)
    note = f"display notification {json.dumps(message)} with title {json.dumps(_TITLE)} sound name \"Glass\""
    plist = {
        "Label": label,
        "ProgramArguments": ["/bin/sh", "-c",
                             f"/usr/bin/osascript -e {shlex_quote(note)}; "
                             f"launchctl unload {shlex_quote(str(agent))}; rm -f {shlex_quote(str(agent))}"],
        "StartCalendarInterval": {"Month": when.month, "Day": when.day, "Hour": when.hour, "Minute": when.minute},
    }
    agent.write_bytes(plistlib.dumps(plist))
    done = subprocess.run(["launchctl", "load", str(agent)], capture_output=True, text=True)
    if done.returncode != 0:
        agent.unlink(missing_ok=True)
        raise RuntimeError(done.stderr.strip() or "launchctl refused the agent")


def shlex_quote(text: str) -> str:
    import shlex
    return shlex.quote(text)


def _via_systemd_or_at(when: datetime, task: str, message: str) -> None:
    notify = ["notify-send", "-u", "normal", _TITLE, message]
    if shutil.which("systemd-run"):
        done = subprocess.run(["systemd-run", "--user", f"--unit={task}",
                               f"--on-calendar={when:%Y-%m-%d %H:%M:00}", "--", *notify],
                              capture_output=True, text=True)
        if done.returncode == 0:
            return
    if shutil.which("at"):
        line = " ".join(shlex_quote(p) for p in notify)
        done = subprocess.run(["at", when.strftime("%H:%M %Y-%m-%d")], input=line + "\n",
                              capture_output=True, text=True)
        if done.returncode == 0:
            return
        raise RuntimeError(done.stderr.strip() or "at refused the job")
    raise RuntimeError("no scheduler available (need systemd-run or at)")


def schedule(when: datetime, message: str) -> None:
    task = f"OmniOS-Reminder-{when:%Y%m%d-%H%M%S}"
    if sys.platform == "win32":
        _via_task_scheduler(when, task, message)
    elif sys.platform == "darwin":
        _via_launchd(when, task, message)
    else:
        _via_systemd_or_at(when, task, message)


@register(
    "reminder",
    "Sets a one-off reminder that pops up as a desktop notification at the given "
    "date and time, even if Omni-OS is closed by then.",
    {
        "date": S("Date, YYYY-MM-DD"),
        "time": S("Time, 24-hour HH:MM"),
        "message": S("What to remind the user about"),
    },
    ["date", "time", "message"],
)
def reminder(args: dict, ctx: ToolContext) -> str:
    date, clock = (args.get("date") or "").strip(), (args.get("time") or "").strip()
    message = " ".join((args.get("message") or "Reminder").split())[:200]
    try:
        when = datetime.strptime(f"{date} {clock}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "I need the date as YYYY-MM-DD and the time as HH:MM."
    if when <= datetime.now():
        return "That time has already passed."
    try:
        schedule(when, message)
    except Exception as err:
        return f"I couldn't schedule that reminder: {err}"
    ctx.log(f"[Reminder] {when:%Y-%m-%d %H:%M} — {message[:40]}")
    return f"Reminder set for {when:%A %d %B at %H:%M}."
