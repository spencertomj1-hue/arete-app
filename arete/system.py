"""
Everything that differs between macOS, Windows and Linux, in one place:
where the data folder is, how to open a folder, and what things are called.
No Streamlit, so store.py and the scripts can use it.
"""

import os
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"


def default_data_dir():
    """Where Arete keeps the user's data, following each system's convention."""
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Arete"
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Arete"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Arete"


def open_folder(path):
    """Show a folder in Finder / File Explorer / the file manager (creating it if needed)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - opens the folder in File Explorer
    else:
        subprocess.run(["open" if IS_MAC else "xdg-open", str(path)], check=False)


# Words for the UI, so messages match the computer they're shown on.
COMPUTER = "Mac" if IS_MAC else "PC" if IS_WINDOWS else "computer"
FILE_MANAGER = "Finder" if IS_MAC else "File Explorer" if IS_WINDOWS else "your file manager"
SECRET_STORE = ("macOS Keychain" if IS_MAC else "Windows Credential Manager" if IS_WINDOWS
                else "system keyring")


# ---------------------------------------------------------------------------
# Desktop notifications
# ---------------------------------------------------------------------------

# Windows: a toast through PowerShell (built in), shown as from PowerShell so no app
# registration is needed. The text comes in through environment variables, never
# pasted into the script, so nothing in a block's name can run as code.
_WINDOWS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$text = $xml.GetElementsByTagName('text')
$text.Item(0).AppendChild($xml.CreateTextNode($env:ARETE_TITLE)) > $null
$text.Item(1).AppendChild($xml.CreateTextNode($env:ARETE_BODY)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def notification_command(title, body):
    """(argv, extra environment) that shows a notification on this system, or None if there's no way."""
    if IS_MAC:
        # The text goes in as arguments (argv), not into the AppleScript itself.
        return (["osascript", "-e", "on run argv", "-e",
                 "display notification (item 2 of argv) with title (item 1 of argv)",
                 "-e", "end run", title, body], {})
    if IS_WINDOWS:
        return (["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", _WINDOWS_TOAST], {"ARETE_TITLE": title, "ARETE_BODY": body})
    import shutil
    if shutil.which("notify-send"):
        return (["notify-send", "--app-name=Arete", title, body], {})
    return None


def notify(title, body=""):
    """Show a desktop notification; True if it was handed to the system."""
    cmd = notification_command(str(title), str(body))
    if not cmd:
        return False
    argv, env = cmd
    flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0   # no console window flashing up
    try:
        done = subprocess.run(argv, env={**os.environ, **env}, capture_output=True, timeout=30,
                              creationflags=flags, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0
