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
  "stagnation_limit": 3
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

step "Installing agent skills"
installed=0
for target in "$HOME/.codex/skills" "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.gemini/skills"; do
  [ -d "$target" ] || continue
  for skill in "$SRC_ROOT"/skills/*/; do
    [ -d "$skill" ] || continue
    name="$(basename "$skill")"
    mkdir -p "$target/$name"
    cp -R "$skill"* "$target/$name/"
    installed=$((installed + 1))
  done
done
if [ "$installed" -gt 0 ]; then ok "$installed skill folder(s) installed"; else warn "no skill target directory found"; fi

step "Registering the MCP server"
codex_config="$HOME/.codex/config.toml"
if [ -f "$codex_config" ]; then
  if grep -q '\[mcp_servers\.mas_loop\]' "$codex_config"; then
    ok "already registered in ~/.codex/config.toml"
  else
    cp "$codex_config" "$codex_config.bak-$(date +%Y%m%d-%H%M%S)"
    cat >> "$codex_config" <<MCP

[mcp_servers.mas_loop]
command = "$PYTHON_BIN"
args = ["-m", "mas", "serve"]
startup_timeout_sec = 30.0
tool_timeout_sec = 300.0
env = { PYTHONPATH = "$APP_DIR" }
MCP
    ok "registered in ~/.codex/config.toml (backup kept)"
  fi
fi

claude_config="$HOME/.claude.json"
if [ -f "$claude_config" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$claude_config" "$PYTHON_BIN" "$APP_DIR" <<'PY'
import json, sys
path, python_bin, app_dir = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
servers = data.setdefault("mcpServers", {})
if "mas_loop" not in servers:
    servers["mas_loop"] = {
        "command": python_bin,
        "args": ["-m", "mas", "serve"],
        "env": {"PYTHONPATH": app_dir},
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    print("[OK] registered in ~/.claude.json")
else:
    print("[OK] already registered in ~/.claude.json")
PY
fi

step "Running the self-check"
export MAS_STATE_DIR="$INSTALL_DIR"
export PYTHONPATH="$APP_DIR"
"$PYTHON_BIN" -m mas doctor || warn "doctor reported issues"

step "Running the offline demo (no API key needed)"
"$PYTHON_BIN" -m mas demo || warn "demo reported issues"

printf '\nInstallation finished.\n\nNext steps:\n'
printf '  1) open a new terminal, then run:  mas demo\n'
printf '  2) point it at a real model:\n'
printf '       mas init --provider openai_compat --model <model> --base-url <endpoint>\n'
printf '       export MAS_API_KEY=<your key>\n'
printf '  3) run a task:  mas run --goal "..." --criteria "..."\n\n'