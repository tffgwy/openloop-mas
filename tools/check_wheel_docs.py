"""Assert that a built wheel really carries the onboarding documents.

The onboarding gate (``mas selftest --only onboarding_path``) watches the
README and the quickstart. A wheel that does not carry them leaves an
installed runtime with nothing to watch, and the install then reports a
failure that says nothing about the beginner - which is worse than no
gate at all, because it looks like a defect.

So the wheel is opened and looked at. This runs in CI right after the
distribution is built, before anything is published.

Usage:
    python tools/check_wheel_docs.py dist/*.whl
"""

from __future__ import annotations

import pathlib
import sys
import zipfile

#: The documents the wheel has to carry.
REQUIRED = (
    "mas/docs/README.md",
    "mas/docs/README.en.md",
    "mas/docs/quickstart.md",
)


def check(wheel: pathlib.Path) -> int:
    if not wheel.exists():
        print(f"[FAIL] no wheel at {wheel}")
        return 1
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = [name for name in REQUIRED if name not in names]
        if missing:
            print(f"[FAIL] {wheel.name} does not carry: {', '.join(missing)}")
            print("       the onboarding gate would have no subject to watch")
            return 1
        # The packaged copy must not be an empty placeholder.
        for name in REQUIRED:
            if not archive.read(name).strip():
                print(f"[FAIL] {wheel.name} carries an empty {name}")
                return 1
    print(f"[OK] {wheel.name} carries the onboarding documents")
    return 0


def main(argv: list[str]) -> int:
    wheels = [pathlib.Path(a) for a in argv[1:] if a.endswith(".whl")]
    if not wheels:
        print(__doc__)
        return 2
    failed = 0
    for wheel in wheels:
        failed += check(wheel)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
