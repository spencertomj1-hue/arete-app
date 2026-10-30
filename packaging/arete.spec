# PyInstaller recipe for the Windows app: builds dist/Arete/Arete.exe (a folder
# with Python and every package inside, so nothing else needs installing).
# Run on Windows from the project folder:
#   pyinstaller packaging/arete.spec --noconfirm
# The GitHub workflow .github/workflows/release.yml does this, then wraps the
# folder into Arete-Setup.exe with Inno Setup (packaging/arete.iss).
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "app.py"), "."),                                     # Streamlit runs it as a file
    (str(ROOT / ".streamlit" / "config.toml"), ".streamlit"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "arete" / "ui" / "components"), "arete/ui/components"),
]
binaries = []
hiddenimports = collect_submodules("arete")    # app.py is data, so its imports are listed here

# Packages with data files, plugins or entry points that PyInstaller can't find by itself.
for package in ["streamlit", "plotly", "keyring", "webview", "claude_agent_sdk", "faster_whisper",
                "ctranslate2", "sounddevice", "_sounddevice_data", "icalendar", "recurring_ical_events",
                "tzdata"]:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Arete",
    icon=str(ROOT / "assets" / "icon.ico"),
    console=False,                 # a normal windowed app; output goes to the data folder's logs/
)
coll = COLLECT(exe, a.binaries, a.datas, name="Arete")
