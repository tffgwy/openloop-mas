"""Make the Pages artifact visible exactly once, before the deploy reads it.

The documentation site is deployed by two separate GitHub Actions:
``upload-pages-artifact`` writes it, ``deploy-pages`` reads it back by
name. Between those two steps the artifact has to be *listed* by the
API - and that listing is not instantaneous. The deploy step can and
does ask the API a few hundred milliseconds after the upload reported
success, get an empty list, and fail the whole deployment with

    Found 0 artifact(s)
    Error: No artifacts named "github-pages" were found for this workflow run.

That is a visibility race, not a broken site: the very same run does
have the artifact, listed a moment later. The site is fine and the
deployment is red.

The opposite failure exists too. Re-running a failed workflow attempt
does not replace the artifact of the previous attempt - it *adds* a
second one under the same name, and the deploy step then refuses to
guess:

    Found 2 artifact(s)
    Error: Multiple artifacts named "github-pages" were unexpectedly found.

So this guard sits between the two steps and does both halves of the
job:

* it waits until the artifact is actually listed by the API, so the
  deploy step never races the index;
* it deletes every same-named artifact except the newest one, so the
  deploy step never has to choose between two.

The decision logic is kept pure and separate from the ``gh`` calls so
that it can be tested without a network: ``tests/test_pages_artifact_guard.py``
feeds it hand-built artifact lists.

Usage:
    python tools/pages_artifact_guard.py --repo owner/name --run-id 123
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

#: The name the Pages deployment looks the artifact up by.
ARTIFACT_NAME = "github-pages"

#: How long to wait for the upload to become visible. The upload step
#: itself reports success *before* the artifact is listed, so the wait
#: is short in practice - but a slow API must not turn into a red run.
DEFAULT_WAIT_SECONDS = 60.0

#: How long to wait between two polls of the listing API.
POLL_INTERVAL_SECONDS = 2.0


class GuardError(RuntimeError):
    """Something went wrong that the caller has to report as a failure."""


def newest(artifacts: list[dict]) -> dict | None:
    """Return the newest artifact of a list, or ``None`` when it is empty.

    "Newest" means the highest artifact id: the ids are handed out in
    increasing order, so the highest one is the one that was uploaded
    last - which is the one this run wants to deploy.
    """
    if not artifacts:
        return None
    return max(artifacts, key=lambda item: int(item.get("id") or 0))


def stale_ids(artifacts: list[dict], keep_id: int | None = None) -> list[int]:
    """Return the ids of the artifacts that must be deleted.

    Only the newest artifact *of the deployed name* survives; the
    artifacts of other names belong to other jobs and are never
    touched.

    When ``keep_id`` is given that artifact is kept even if it is not
    the newest - the caller uses it to protect the artifact it has
    just uploaded.
    """
    keep = newest(named(artifacts))
    if keep is None:
        return []
    kept = int(keep.get("id") or 0)
    if keep_id is not None:
        kept = int(keep_id)
    return [
        int(a.get("id") or 0)
        for a in named(artifacts)
        if int(a.get("id") or 0) != kept
    ]


def named(artifacts: list[dict], name: str = ARTIFACT_NAME) -> list[dict]:
    """Return the artifacts called ``name``."""
    return [a for a in artifacts if str(a.get("name") or "") == name]


def artifact_id(artifacts: list[dict], name: str = ARTIFACT_NAME) -> int | None:
    """Return the id of the single artifact called ``name``, or ``None``.

    ``None`` means "not listed yet" (the deploy step must wait). More
    than one match raises, because the extras have to go first and the
    caller has to know that they are in the way.
    """
    matches = named(artifacts, name)
    if not matches:
        return None
    if len(matches) > 1:
        raise GuardError(
            "%d artifacts are named %r; the extras must be deleted first" % (len(matches), name)
        )
    return int(matches[0].get("id") or 0)


def _gh(args: list[str], token: str | None = None) -> str:
    """Run a ``gh`` command and return its standard output."""
    env = None
    if token:
        env = dict(os.environ)
        env["GH_TOKEN"] = token
    try:
        done = subprocess.run(
            ["gh", *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
    except FileNotFoundError as exc:
        raise GuardError("the gh command line is not available") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise GuardError("gh %s failed: %s" % (" ".join(args), detail)) from exc
    return done.stdout


def list_artifacts(repo: str, run_id: str, token: str | None = None) -> list[dict]:
    """List the artifacts of a workflow run."""
    raw = _gh(
        ["api", "repos/%s/actions/runs/%s/artifacts" % (repo, run_id), "--paginate"],
        token=token,
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuardError("the artifact listing was not JSON: %s" % exc) from exc
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list):
        raise GuardError("the artifact listing had no artifact list")
    return [a for a in artifacts if isinstance(a, dict)]


def delete_artifact(repo: str, artifact_id: int, token: str | None = None) -> None:
    """Delete one artifact of the repository."""
    _gh(
        ["api", "-X", "DELETE", "repos/%s/actions/artifacts/%d" % (repo, artifact_id)],
        token=token,
    )


def wait_for_visibility(
    repo: str,
    run_id: str,
    token: str | None = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    sleep=time.sleep,
    now=time.monotonic,
) -> list[dict]:
    """Wait until the artifact is listed, and return the listing.

    The wait ends in one of two ways:

    * at least one artifact of the name is listed - success;
    * the wait times out with nothing listed - failure, because the
      upload did not happen at all.

    More than one listed is *not* an error here: the extras are the
    stale ones a re-run left behind, and removing them is exactly what
    the caller does next.
    """
    deadline = now() + max(0.0, wait_seconds)
    listing: list[dict] = []
    while True:
        listing = list_artifacts(repo, run_id, token=token)
        if named(listing):
            return listing
        if now() >= deadline:
            raise GuardError(
                "no %r artifact was listed in run %s within %gs"
                % (ARTIFACT_NAME, run_id, wait_seconds)
            )
        sleep(POLL_INTERVAL_SECONDS)


def guard(
    repo: str,
    run_id: str,
    token: str | None = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    sleep=time.sleep,
    now=time.monotonic,
    deleter=None,
) -> dict:
    """Make sure exactly one deployable artifact exists, then say so."""
    listing = wait_for_visibility(
        repo, run_id, token=token, wait_seconds=wait_seconds, sleep=sleep, now=now
    )
    matches = named(listing)
    keep = newest(matches)
    removed: list[int] = []
    for stale in stale_ids(matches):
        if deleter is None:
            delete_artifact(repo, stale, token=token)
        else:
            deleter(stale)
        removed.append(stale)
    return {
        "kept": int(keep.get("id") or 0) if keep else None,
        "deleted": removed,
        "listed": len(listing),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Guard the Pages artifact.")
    parser.add_argument("--repo", required=True, help="owner/name of the repository")
    parser.add_argument("--run-id", required=True, help="the workflow run to guard")
    parser.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT_SECONDS)
    args = parser.parse_args(argv[1:])
    try:
        outcome = guard(args.repo, args.run_id, wait_seconds=args.wait_seconds)
    except GuardError as exc:
        print("[FAIL] %s" % exc)
        return 1
    print(
        "[OK] exactly one %r artifact is visible (id %s), %d stale one(s) deleted"
        % (ARTIFACT_NAME, outcome["kept"], len(outcome["deleted"]))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
