#!/usr/bin/env bash
# One-click installer for OpenLoop MAS (multi-agent loop runtime).
# Pure standard library runtime: no pip install, no virtualenv, no root.
set -euo pipefail

INSTALL_DIR="${MAS_HOME:-$HOME/.mas}"
APP_DIR="$INSTALL_DIR/app"
BIN_DIR="$INSTALL_DIR/bin"
SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN=""

step() { printf '[*] %s\n' "$1"; }
ok()   { printf '[OK] %s\n' "$1"; }
warn() { printf '[!!] %s\n' "$1"; }
fail() { printf '[XX] %s\n' "$1"; exit 1; }

printf '\nOpenLoop MAS - one-click installer\nsource: %s\n\n' "$SRC_ROOT"

step "Locating Python 3.10+"
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$cand")"
      break
    fi
  fi
done
[ -n "$PYTHON_BIN" ] || fail "Python 3.10+ not found. Install it, then re-run this script."
ok "Python $("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') at $PYTHON_BIN"

step "Installing runtime into $APP_DIR"
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/mas"
cp -R "$SRC_ROOT/mas" "$APP_DIR/mas"
find "$APP_DIR/mas" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
ok "runtime copied"

step "Writing configuration"
if [ ! -f "$INSTALL_DIR/config.json" ]; then
  cat > "$INSTALL_DIR/config.json" <<JSON
{
  "schema_version": 1,
  "provider": "mock",
  "model": "mock-1",
  "base_url": "",
  "api_key": "",
  "api_key_env": "MAS_API_KEY",
  "timeout_sec": 120,
  "max_retries": 2,
  "state_dir": "$INSTALL_DIR",
  "team": "default",
  "language": "zh",
  "budget": { "max_iterations": 8, "deadline_sec": 900, "max_tokens": 200000 },
  "stagnation_limit": 3,
"orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
"fallbacks": []
}
JSON
  ok "config created (offline mock provider)"
else
  ok "config already exists, kept as is"
fi

step "Creating launcher"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/mas" <<LAUNCH
#!/usr/bin/env bash
export MAS_HOME="$INSTALL_DIR"
export PYTHONPATH="$APP_DIR:\${PYTHONPATH:-}"
exec "$PYTHON_BIN" -m mas "\$@"
LAUNCH
chmod +x "$BIN_DIR/mas"
ok "launcher: $BIN_DIR/mas"

step "Adding $BIN_DIR to PATH"
if echo "${PATH:-}" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
  ok "already in PATH"
else
  for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    if [ -f "$rc" ] || [ "$rc" = "$HOME/.bashrc" ]; then
      printf '\n# OpenLoop MAS\nexport PATH="$BIN_DIR:$PATH"\n' >> "$rc"
    fi
  done
  ok "added to shell rc files (new terminals will see it)"
fi

step "Installing agent skills and registering the MCP server"
# One implementation, one behaviour: the python installer is the single
# source of truth for both the skills and the MCP registration. The shell
# script only wires the environment up (PYTHONPATH / MAS_STATE_DIR) and
# calls it, so the two install paths can never drift apart.
export MAS_STATE_DIR="$INSTALL_DIR"
export PYTHONPATH="$APP_DIR"
if "$PYTHON_BIN" -m mas install --python "$PYTHON_BIN"; then
  ok "skills installed and MCP server registered"
else
  warn "the installer reported problems - check the output above"
fi


step "Running the self-check"
export MAS_STATE_DIR="$INSTALL_DIR"
export PYTHONPATH="$APP_DIR"
"$PYTHON_BIN" -m mas doctor || warn "doctor reported issues"

step "Running the offline demo (no API key needed)"
"$PYTHON_BIN" -m mas demo || warn "demo reported issues"

step "Running the offline benchmark"
"$PYTHON_BIN" -m mas bench --repeats 1 || warn "bench reported issues"

step "Running the closure self-check"
"$PYTHON_BIN" -m mas verify || warn "verify reported issues"

printf '\nInstallation finished.\n\nNext steps:\n'
printf '  1) open a new terminal, then run:  mas demo\n'
printf '  2) configure interactively:  mas init --wizard\n'
printf '  3) point it at a real model:\n'
printf '       mas init --provider openai_compat --model <model> --base-url <endpoint>\n'
printf '       export MAS_API_KEY=<your key>\n'
printf '  4) run a task:  mas run --goal "..." --criteria "..."\n'
printf '  5) review the result:  mas report    (or: mas replay)\n'
printf '  6) serve it remotely:  mas http --port 8765\n\n'