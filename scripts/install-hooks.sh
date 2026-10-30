#!/bin/bash
# Installs a git pre-commit hook that runs gitleaks on staged changes, so secrets
# and personal data can't be committed by accident. Run once per clone.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v gitleaks >/dev/null || { echo "Install gitleaks first: brew install gitleaks"; exit 1; }
cat > .git/hooks/pre-commit <<'HOOK'
#!/bin/bash
# Block commits that contain secrets (installed by scripts/install-hooks.sh).
exec gitleaks git --pre-commit --staged --redact --no-banner
HOOK
chmod +x .git/hooks/pre-commit
echo "gitleaks pre-commit hook installed."
