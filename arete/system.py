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
