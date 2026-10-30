#!/bin/bash
# One-command setup for Arete on macOS:
#   1. checks this is a Mac with Python 3.13
#   2. creates .venv and installs the pinned Python packages (requirements.txt)
#   3. builds Arete.app (in /Applications, or ~/Applications if that isn't writable)
#
# Run from the project folder:   ./setup.sh
# Safe to run again (e.g. after pulling updates).
set -euo pipefail

cd "$(dirname "$0")"
APP_DIR="$(pwd)"

say()  { printf "\n\033[1m%s\033[0m\n" "$1"; }
fail() { printf "\n\033[31m%s\033[0m\n" "$1"; exit 1; }

# --- 1. macOS and Python ----------------------------------------------------
say "1/3  Checking macOS and Python…"
[ "$(uname -s)" = "Darwin" ] || fail "setup.sh is for macOS. On Windows, run setup.ps1 instead."
echo "macOS $(sw_vers -productVersion)"
PY=""
for candidate in python3.13 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 13))' 2>/dev/null; then
    PY="$(command -v "$candidate")"; break
  fi
done
[ -n "$PY" ] || fail "Arete needs Python 3.13 (the version its packages are tested with).
Install it from https://www.python.org/downloads/ (or with Homebrew: brew install python@3.13), then run ./setup.sh again."
echo "Using $("$PY" --version) at $PY"

# --- 2. Packages -------------------------------------------------------------
say "2/3  Installing packages (a few minutes the first time)…"
# Reuse an existing .venv only if it's Python 3.13; otherwise make a fresh one.
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 13))' 2>/dev/null; then
  echo "Replacing .venv (it isn't Python 3.13)"; rm -rf .venv
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
echo "Packages installed."

# --- 3. Arete.app ------------------------------------------------------------
say "3/3  Building Arete.app…"
if [ -n "${ARETE_APP_PATH:-}" ]; then
  APP="$ARETE_APP_PATH"
elif [ -w /Applications ]; then
  APP="/Applications/Arete.app"
else
  mkdir -p "$HOME/Applications"; APP="$HOME/Applications/Arete.app"
fi
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp assets/Arete.icns "$APP/Contents/Resources/Arete.icns"

cat > "$APP/Contents/MacOS/Arete" <<EOF
#!/bin/zsh
# Launches the Arete desktop window using the project's virtualenv.
APP_DIR="$APP_DIR"
exec "\$APP_DIR/.venv/bin/python" "\$APP_DIR/desktop.py"
EOF
chmod +x "$APP/Contents/MacOS/Arete"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Arete</string>
    <key>CFBundleDisplayName</key><string>Arete</string>
    <key>CFBundleIdentifier</key><string>local.arete</string>
    <key>CFBundleExecutable</key><string>Arete</string>
    <key>CFBundleIconFile</key><string>Arete</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>Arete records your voice when you click the mic in the chat bar, and transcribes it on this Mac.</string>
</dict>
</plist>
EOF
# Tell macOS about the app (and its icon) straight away.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
echo "Built $APP"

say "All done!"
cat <<EOF
Open Arete from your Applications folder, Launchpad or Spotlight.
  • The first time, macOS may say it's from an unidentified developer:
    right-click Arete → Open → Open.
  • A short setup guide runs on first launch (Claude, Outlook calendar, microphone).
EOF
