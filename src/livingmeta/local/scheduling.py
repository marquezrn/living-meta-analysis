"""Opt-in native scheduler templates; scientific due times use IANA timezone rules."""

import hashlib
import html
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from .workspace import load_manifest, safe_path


def write_templates(workspace: Path, contact_email: str) -> dict:
    workspace = Path(workspace).expanduser().resolve()
    load_manifest(workspace)
    if not contact_email or "@" not in contact_email or any(c in contact_email for c in "\r\n\0"):
        raise ValueError("A valid contact email is required for public metadata requests")
    identifier = "org.livingmeta.monitor." + hashlib.sha256(str(workspace).encode()).hexdigest()[:12]
    arguments = [sys.executable, "-m", "livingmeta.cli", "monitor-due", "--workspace", str(workspace),
                 "--contact-email", contact_email]
    folder = safe_path(workspace, "schedule")
    folder.mkdir(parents=True, exist_ok=True)
    plist = {"Label": identifier, "ProgramArguments": arguments, "RunAtLoad": True,
             "StartCalendarInterval": {"Minute": 0}, "StandardOutPath": str(folder / "monitor.log"),
             "StandardErrorPath": str(folder / "monitor-error.log")}
    (folder / "monitor.plist").write_bytes(plistlib.dumps(plist))
    # Escape '%' for cron, which otherwise treats it as an input separator.
    command = shlex.join(arguments).replace("%", "\\%")
    (folder / "monitor.cron").write_text(f"0 * * * * {command}\n", encoding="utf-8")
    windows_args = subprocess.list2cmdline(arguments[1:])
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
<RegistrationInfo><Description>Metadata-only local living review check; no agent extraction.</Description></RegistrationInfo>
<Triggers><TimeTrigger><Repetition><Interval>PT1H</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
<StartBoundary>2026-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger></Triggers>
<Settings><StartWhenAvailable>true</StartWhenAvailable><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
<ExecutionTimeLimit>PT30M</ExecutionTimeLimit></Settings>
<Actions Context=""><Exec><Command>{html.escape(sys.executable)}</Command>
<Arguments>{html.escape(windows_args)}</Arguments></Exec></Actions></Task>'''
    (folder / "monitor-task.xml").write_text(xml, encoding="utf-8")
    instructions = f'''# Optional local scheduling

No task has been registered. These templates call monitor-due hourly. The toolkit
performs one check only when Monday 08:00 Europe/Madrid is due, including one overdue
catch-up after missed execution. The computer must be available. Extraction is never
scheduled. Logs and contact information remain private in this workspace.

macOS: copy monitor.plist to ~/Library/LaunchAgents/{identifier}.plist, then load it
through launchctl bootstrap gui/USER_ID. Keep the absolute Python environment path
valid. Remove/unload that LaunchAgent to disable it.

Linux: inspect monitor.cron and add its line through crontab -e. Remove the line to
disable it. Run monitor-due once after startup if a check was missed while offline.

Windows: import monitor-task.xml using Task Scheduler and select your own current
user account. Do not store another person's credentials. Disable/delete the task
through Task Scheduler to stop it.

The command is:
{shlex.join(arguments)}
'''
    (folder / "README.md").write_text(instructions, encoding="utf-8")
    return {"schedule_directory": str(folder), "registered": False,
            "scientific_schedule": "Monday 08:00 Europe/Madrid", "trigger_interval": "hourly",
            "model_calls": 0}
