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
# MAS_PYTHON is the counterpart of the PowerShell installer's -Python
# switch: it names the interpreter to use instead of searching PATH.
if [ -n "${MAS_PYTHON:-}" ]; then
  if [ -x "$MAS_PYTHON" ] && "$MAS_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
    PYTHON_BIN="$MAS_PYTHON"
  else
    fail "MAS_PYTHON is set to '$MAS_PYTHON' but it is not a usable Python 3.10+"
  fi
fi
if [ -z "$PYTHON_BIN" ]; then
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v "$cand")"
        break
      fi
    fi
  done
fi
[ -n "$PYTHON_BIN" ] || fail "Python 3.10+ not found. Install it, then re-run this script."
ok "Python $("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') at $PYTHON_BIN"

# The interpreter path is normalised to an absolute path *before* it is
# written into the MCP registration. The registration is read by a host
# process (Codex / Claude Code), not by this script: on Windows the shell
# resolves ``python`` to a POSIX path such as ``/c/Python314/python`` that
# no Windows process can execute.
PYTHON_BIN="$("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
ok "interpreter: $PYTHON_BIN"

step "Installing runtime into $APP_DIR"
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/mas"
if [ -d "$SRC_ROOT/mas" ]; then
  # A source checkout is available: copy the runtime from it.
  cp -R "$SRC_ROOT/mas" "$APP_DIR/mas"
  find "$APP_DIR/mas" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  ok "runtime copied from source checkout"
else
  # Standalone script: there is no package source next to this file, so the
  # runtime is fetched from the published wheel and unpacked by hand. This is
  # the path a beginner takes when they download install.sh alone, so it must
  # work without git, pip, or a checkout.
  step "No source checkout found - fetching the runtime wheel"
  REPO="tffgwy/openloop-mas"
  VERSION="${MAS_VERSION:-latest}"
  WHEEL_PATH="$INSTALL_DIR/openloop_mas.whl"
  HELPER_PATH="$INSTALL_DIR/_download_helper.py"

  if [ -n "${MAS_WHEEL:-}" ]; then
    # A wheel was supplied explicitly. This is how the release pipeline and
    # the CI install matrix exercise a build that has not been published yet.
    # The download path is then never exercised, so the two paths have to
    # stay behaviourally identical.
    [ -f "$MAS_WHEEL" ] || fail "wheel not found: $MAS_WHEEL"
    cp "$MAS_WHEEL" "$WHEEL_PATH"
    ok "using the supplied wheel: $MAS_WHEEL"
  fi

  # The helper carries the download logic. It exists because the naive
  # version breaks on real machines:
  #
  # * The GitHub REST API allows only 60 anonymous requests per hour and
  #   per IP, and a shared network can burn that budget before the user
  #   starts. The release *page* answers with a redirect to the newest
  #   tag and is not rate limited, so the version comes from there.
  # * curl is not installed everywhere, and the standard library always
  #   is. Both transports are tried, and the first one that works wins.
  # * A TLS-inspecting proxy can break one transport and not the other.
  cat > "$HELPER_PATH" <<'HELPER'
"""Download helper for the one-click installer.

Three things make a naive download fail on a real machine, so the
helper works around each of them:

* **TLS interception.** A corporate or regional proxy can break the
  standard library's TLS stack while ``curl`` negotiates the very same
  connection fine. Both transports are tried, and the first one that
  works wins.
* **API rate limits.** The GitHub REST API allows only 60 anonymous
  requests per hour and per IP, and a shared network can burn that
  budget before the user even starts. The ``releases/latest`` *page*
  answers with a redirect to the newest tag and is not rate limited,
  so the version is resolved from there.
* **No curl.** ``curl`` is missing on a default Windows install, and
  the standard library is always there. The order is therefore
  standard library first, curl second.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "tffgwy/openloop-mas"
LATEST_PAGE = "https://github.com/" + REPO + "/releases/latest"


def _proxy_url():
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(name)
        if value:
            return value
    return ""


def _curl():
    return shutil.which("curl.exe") or shutil.which("curl")


def _opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(urllib.request.getproxies())
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read(url, timeout, follow=True):
    if follow:
        opener = _opener()
    else:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(urllib.request.getproxies()),
            _NoRedirect,
        )
    request = urllib.request.Request(
        url, headers={"User-Agent": "openloop-mas-installer"}
    )
    return opener.open(request, timeout=timeout)


def _fetch_bytes(url, timeout):
    with _read(url, timeout) as response:
        return response.read()


def _curl_run(url, dest, timeout=600):
    binary = _curl()
    if not binary:
        raise RuntimeError("curl is not available")
    command = [binary, "-fsSL", "--retry", "3"]
    proxy = _proxy_url()
    if proxy:
        command += ["-x", proxy]
    command += ["-o", str(dest), url]
    subprocess.run(command, check=True, timeout=timeout)
    return pathlib.Path(dest).stat().st_size


def download(url, dest):
    errors = []
    try:
        data = _fetch_bytes(url, 300)
        pathlib.Path(dest).write_bytes(data)
        return len(data)
    except Exception as exc:
        errors.append("urllib: {}: {}".format(type(exc).__name__, exc))
    if _curl():
        try:
            return _curl_run(url, dest)
        except Exception as exc:
            errors.append("curl: {}: {}".format(type(exc).__name__, exc))
    for line in errors:
        print(line, file=sys.stderr)
    raise SystemExit(1)


def latest_version():
    """Resolve the newest published version without the REST API.

    The API is rate limited to 60 anonymous requests per hour; the
    release page redirects to the newest tag and is not.
    """
    errors = []
    try:
        try:
            _read(LATEST_PAGE, 60, follow=False)
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") or ""
            match = re.search(r"/tag/v?([0-9][^/]*)", location)
            if match:
                return match.group(1)
            errors.append("urllib: no tag in redirect {}".format(location))
        else:
            errors.append("urllib: no redirect")
    except Exception as exc:
        errors.append("urllib: {}: {}".format(type(exc).__name__, exc))
    if _curl():
        try:
            raw = subprocess.run(
                [_curl(), "-fsSL", "--retry", "3", "-o", os.devnull, "-w",
                 "%{url_effective}", LATEST_PAGE],
                check=True, timeout=120, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout.decode("utf-8", "replace")
            match = re.search(r"/tag/v?([0-9][^/]*)", raw)
            if match:
                return match.group(1)
            errors.append("curl: no tag in {}".format(raw))
        except Exception as exc:
            errors.append("curl: {}: {}".format(type(exc).__name__, exc))
    for line in errors:
        print(line, file=sys.stderr)
    raise SystemExit(1)


def wheel_url(version):
    return (
        "https://github.com/" + REPO + "/releases/download/v{}/"
        "openloop_mas-{}-py3-none-any.whl".format(version, version)
    )


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "latest":
        print(wheel_url(latest_version()))
        raise SystemExit(0)
    if mode == "fetch":
        print(download(sys.argv[2], sys.argv[3]))
        raise SystemExit(0)
    raise SystemExit(2)
HELPER

  if [ -z "${MAS_WHEEL:-}" ]; then
    WHEEL_URL=""
    if [ "$VERSION" = "latest" ]; then
      WHEEL_URL="$("$PYTHON_BIN" "$HELPER_PATH" latest 2>/dev/null || true)"
    else
      WHEEL_URL="https://github.com/$REPO/releases/download/v$VERSION/openloop_mas-$VERSION-py3-none-any.whl"
    fi
    [ -n "$WHEEL_URL" ] || fail "could not determine the wheel URL. Set MAS_VERSION explicitly, e.g. MAS_VERSION=0.7.17"
    "$PYTHON_BIN" "$HELPER_PATH" fetch "$WHEEL_URL" "$WHEEL_PATH" \
        || fail "could not download the wheel from $WHEEL_URL"
    ok "wheel downloaded ($(wc -c < "$WHEEL_PATH") bytes)"
  fi

  # A wheel is a zip; unpack it with the standard library so the installer
  # stays dependency-free (no pip, no unzip quirks).
  EXTRACT_DIR="$INSTALL_DIR/_wheel"
  rm -rf "$EXTRACT_DIR"
  mkdir -p "$EXTRACT_DIR"
  "$PYTHON_BIN" -c 'import pathlib,sys,zipfile
src=pathlib.Path(sys.argv[1]); dst=pathlib.Path(sys.argv[2])
with zipfile.ZipFile(src) as zf: zf.extractall(dst)' "$WHEEL_PATH" "$EXTRACT_DIR" \
    || fail "could not unpack the wheel"
  [ -d "$EXTRACT_DIR/mas" ] || fail "the wheel did not contain the mas package"
  mv "$EXTRACT_DIR/mas" "$APP_DIR/mas"
  rm -rf "$EXTRACT_DIR" "$WHEEL_PATH" "$HELPER_PATH"
  ok "runtime unpacked from the published wheel"
fi

step "Writing configuration"
if [ ! -f "$INSTALL_DIR/config.json" ]; then
  # The configuration is written by Python, not by a heredoc. A heredoc
  # writes the path literally, and a Windows path contains backslashes -
  # which turns the file into invalid JSON ("\U" is not a legal escape).
  # json.dumps does the escaping correctly on every platform.
  "$PYTHON_BIN" - "$INSTALL_DIR" <<'CONFIG'
import json, pathlib, sys

install_dir = sys.argv[1]
config = {
    "schema_version": 1,
    "provider": "mock",
    "model": "mock-1",
    "base_url": "",
    "api_key": "",
    "api_key_env": "MAS_API_KEY",
    "timeout_sec": 120,
    "max_retries": 2,
    "state_dir": install_dir,
    "team": "default",
    "language": "zh",
    "budget": {"max_iterations": 8, "deadline_sec": 900, "max_tokens": 200000},
    "stagnation_limit": 3,
    "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
    "fallbacks": [],
}
pathlib.Path(install_dir, "config.json").write_text(
    json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
CONFIG
  ok "config created (offline mock provider)"
else
  ok "config already exists, kept as is"
fi

step "Creating launcher"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/mas" <<LAUNCH
#!/usr/bin/env bash
export MAS_HOME="$INSTALL_DIR"
export MAS_STATE_DIR="$INSTALL_DIR"
export MAS_CONFIG_PATH="$INSTALL_DIR/config.json"
export PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PYTHON_BIN" "$APP_DIR/mas/__main__.py" "\$@"
LAUNCH
chmod +x "$BIN_DIR/mas"
ok "launcher: $BIN_DIR/mas"

# The PATH step is the only part of the installer that touches files outside
# ~/.mas, so it can be turned off. MAS_SKIP_PATH=1 is the counterpart of the
# PowerShell installer's -SkipPath switch: the two installers take the same
# decisions and use the same names for them.
if [ "${MAS_SKIP_PATH:-0}" = "1" ]; then
  step "Skipping the PATH step (MAS_SKIP_PATH=1)"
else
  step "Adding $BIN_DIR to PATH"
  if echo "${PATH:-}" | tr ':' '\n' | grep -Fqx -- "$BIN_DIR"; then
    ok "already in PATH"
  else
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
      if [ -f "$rc" ] || [ "$rc" = "$HOME/.bashrc" ]; then
        printf '\n# OpenLoop MAS\nexport PATH="$BIN_DIR:$PATH"\n' >> "$rc"
      fi
    done
    ok "added to shell rc files (new terminals will see it)"
  fi
fi

step "Installing agent skills and registering the MCP server"
# One implementation, one behaviour: the python installer is the single
# source of truth for both the skills and the MCP registration. The shell
# script only wires the environment up (PYTHONPATH / MAS_STATE_DIR) and
# calls it, so the two install paths can never drift apart.
export MAS_STATE_DIR="$INSTALL_DIR"
export MAS_CONFIG_PATH="$INSTALL_DIR/config.json"
export PYTHONPATH="$APP_DIR"
# The runtime is started through its *package path*, not through
# ``-m mas``. ``python -m`` puts the current working directory first on
# ``sys.path``; when the installer runs inside a source checkout, the
# checkout's ``mas/`` package shadows the one that was just installed.
# The skills and the MCP registration would then come from the
# checkout - so the freshly downloaded wheel was never used at all.
if "$PYTHON_BIN" "$APP_DIR/mas/__main__.py" install --python "$PYTHON_BIN"; then
  ok "skills installed and MCP server registered"
else
  warn "the installer reported problems - check the output above"
fi


step "Running the self-check"
export MAS_STATE_DIR="$INSTALL_DIR"
export PYTHONPATH="$APP_DIR"
MAS_MAIN="$APP_DIR/mas/__main__.py"
"$PYTHON_BIN" "$MAS_MAIN" doctor || warn "doctor reported issues"

step "Running the offline demo (no API key needed)"
"$PYTHON_BIN" "$MAS_MAIN" demo || warn "demo reported issues"

step "Running the offline benchmark"
"$PYTHON_BIN" "$MAS_MAIN" bench --repeats 1 || warn "bench reported issues"

step "Running the closure self-check"
"$PYTHON_BIN" "$MAS_MAIN" verify || warn "verify reported issues"

printf '\nInstallation finished.\n\nNext steps:\n'
printf '  1) open a new terminal, then run:  mas demo\n'
printf '  2) configure interactively:  mas init --wizard\n'
printf '  3) point it at a real model:\n'
printf '       mas init --provider openai_compat --model <model> --base-url <endpoint>\n'
printf '       export MAS_API_KEY=<your key>\n'
printf '  4) run a task:  mas run --goal "..." --criteria "..."\n'
printf '  5) review the result:  mas report    (or: mas replay)\n'
printf '  6) serve it remotely:  mas http --port 8765\n\n'
