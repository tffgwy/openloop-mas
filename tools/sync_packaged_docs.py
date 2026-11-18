"""Copy the onboarding documents into the wheel.

The onboarding gate (``mas selftest --only onboarding_path``) watches
the *front door*: the README and the quickstart. In a source checkout it
can read them from the repository. In a plain ``pip install`` tree the
wheel carries the package, not the repository's prose - so the gate had
no subject and reported a failure that said nothing at all about the
beginner.

So the documents travel with the wheel. This script copies them into
``mas/docs/``; ``tests/test_packaged_docs.py`` asserts the copy is
byte-identical to the source, so drift fails CI instead of shipping.

Usage:
    python tools/sync_packaged_docs.py           # copy
    python tools/sync_packaged_docs.py --check   # verify only
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "mas" / "docs"

#: (source, packaged name) - the document and where it lands in the wheel.
SOURCES = (
    ("README.md", "README.md"),
    ("README.en.md", "README.en.md"),
    ("docs/quickstart.md", "quickstart.md"),
)


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync(check: bool = False) -> int:
    """Copy the documents, or report whether they are in step."""
    problems = []
    for source, name in SOURCES:
        src = ROOT / source
        dst = TARGET / name
        if not src.exists():
            problems.append("missing source: %s" % source)
            continue
        if check:
            if not dst.exists():
                problems.append("not packaged: %s (run without --check)" % name)
            elif _digest(src) != _digest(dst):
                problems.append(
                    "drifted: %s differs from %s (run without --check)" % (name, source)
                )
            continue
        TARGET.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        print("[OK] packaged %s <- %s" % (name, source))

    manifest = TARGET / "MANIFEST.txt"
    if not check:
        manifest.write_text(
            "".join("%s\n" % name for _src, name in SOURCES), encoding="utf-8"
        )

    if problems:
        for problem in problems:
            print("[FAIL] %s" % problem)
        return 1
    if check:
        print("[OK] the packaged documents match the repository")
    return 0


def main(argv: list[str]) -> int:
    check = "--check" in argv[1:]
    return sync(check=check)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
