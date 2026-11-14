"""Assert that an installed runtime is the one built from this checkout.

The standalone installer can be handed a wheel that was built locally. That
is how CI proves the installer installs *exactly* the artefact it was given -
a published wheel cannot prove it, because its version legitimately lags
the repository until a release is cut.

Usage:
    python tools/check_installed_version.py <install-home> <repo-root>
"""
from __future__ import annotations

import pathlib
import re
import sys

VERSION_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')


def read_version(path: pathlib.Path) -> str:
    match = VERSION_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        return "<missing>"
    return match.group(1)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    home = pathlib.Path(argv[1]).resolve()
    repo = pathlib.Path(argv[2]).resolve()

    expected = read_version(repo / "mas" / "__init__.py")
    installed_path = home / ".mas" / "app" / "mas" / "__init__.py"
    if not installed_path.exists():
        print(f"[FAIL] the runtime was not installed at {installed_path}")
        return 1
    actual = read_version(installed_path)
    print(f"repository={expected} installed={actual}")
    if actual != expected:
        print("[FAIL] the installed runtime does not match the repository build")
        return 1
    print("[OK] the installed runtime is the build from this checkout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
