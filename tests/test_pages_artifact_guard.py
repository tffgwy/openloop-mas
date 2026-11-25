# -*- coding: utf-8 -*-
"""The Pages deployment must never race the artifact index.

The documentation site is published by two separate Actions: one
uploads the artifact, the next reads it back *by name*. Two failures
live in that gap, and both of them are GitHub-side races rather than
defects of the site:

* the deploy step asks the API before the upload has been indexed and
  is told "Found 0 artifact(s)" - the run is red while the artifact
  sits there, visible a moment later;
* a re-run of a failed attempt *adds* a second artifact under the same
  name instead of replacing it, and the deploy step then refuses to
  choose - "Found 2 artifact(s)".

This file pins the guard that closes both holes: it waits for the
artifact to be listed, and it leaves exactly one behind.
"""
import pathlib
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import pages_artifact_guard as guard_module  # noqa: E402


def _artifact(artifact_id, name="github-pages"):
    return {"id": artifact_id, "name": name, "size_in_bytes": 10}


class TheNewestArtifactIsTheOneToDeploy(unittest.TestCase):
    def test_the_newest_artifact_wins(self):
        self.assertEqual(guard_module.newest([_artifact(7), _artifact(11)]).get("id"), 11)

    def test_an_empty_listing_has_no_newest(self):
        self.assertIsNone(guard_module.newest([]))

    def test_only_the_old_ones_are_stale(self):
        stale = guard_module.stale_ids([_artifact(7), _artifact(11), _artifact(9)])
        self.assertEqual(sorted(stale), [7, 9])

    def test_a_named_artifact_is_kept_even_when_it_is_not_the_newest(self):
        stale = guard_module.stale_ids([_artifact(7), _artifact(11)], keep_id=7)
        self.assertEqual(stale, [11])

    def test_other_names_are_never_touched(self):
        # The "dist" artifact belongs to another job. The guard deletes
        # stale copies of the *deployed* name only, so it must not even
        # look at the other one.
        stale = guard_module.stale_ids([_artifact(7), _artifact(11, name="dist")])
        self.assertEqual(stale, [])


class TheVisibilityRaceIsWaitedOut(unittest.TestCase):
    def test_the_guard_waits_until_the_artifact_is_listed(self):
        listings = [[], [], [_artifact(42)]]
        calls = {"n": 0}

        def fake_list(repo, run_id, token=None):
            index = min(calls["n"], len(listings) - 1)
            calls["n"] += 1
            return listings[index]

        slept = []
        ticks = iter([0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])

        with mock.patch.object(guard_module, "list_artifacts", fake_list):
            outcome = guard_module.guard(
                "owner/name",
                "123",
                wait_seconds=30,
                sleep=slept.append,
                now=lambda: next(ticks, 100.0),
            )

        self.assertEqual(outcome["kept"], 42)
        self.assertEqual(outcome["deleted"], [])
        self.assertTrue(slept, "the guard must not give up on the first empty listing")

    def test_a_missing_artifact_is_reported_not_ignored(self):
        with (
            mock.patch.object(guard_module, "list_artifacts", lambda *a, **k: []),
            self.assertRaises(guard_module.GuardError),
        ):
            guard_module.guard(
                "owner/name",
                "123",
                wait_seconds=0,
                sleep=lambda _s: None,
                now=lambda: 0.0,
            )


class TheStaleArtifactIsRemoved(unittest.TestCase):
    def test_only_the_newest_survives(self):
        deleted = []
        with mock.patch.object(
            guard_module,
            "list_artifacts",
            lambda *a, **k: [_artifact(14984298801), _artifact(14985114556)],
        ):
            outcome = guard_module.guard(
                "owner/name",
                "123",
                wait_seconds=0,
                sleep=lambda _s: None,
                now=lambda: 0.0,
                deleter=deleted.append,
            )

        self.assertEqual(outcome["kept"], 14985114556)
        self.assertEqual(deleted, [14984298801])

    def test_a_single_artifact_is_never_deleted(self):
        deleted = []
        with mock.patch.object(
            guard_module, "list_artifacts", lambda *a, **k: [_artifact(14984298801)]
        ):
            outcome = guard_module.guard(
                "owner/name",
                "123",
                wait_seconds=0,
                sleep=lambda _s: None,
                now=lambda: 0.0,
                deleter=deleted.append,
            )

        self.assertEqual(outcome["kept"], 14984298801)
        self.assertEqual(deleted, [])

    def test_the_extras_are_named_when_there_are_several(self):
        with self.assertRaises(guard_module.GuardError) as caught:
            guard_module.artifact_id([_artifact(1), _artifact(2)])
        self.assertIn("github-pages", str(caught.exception))


class TheWorkflowUsesTheGuard(unittest.TestCase):
    """A guard nobody calls is not a guard."""

    def test_the_pages_workflow_guards_the_artifact_before_the_deploy(self):
        text = (REPO / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        guard_at = text.find("pages_artifact_guard.py")
        deploy_at = text.find("actions/deploy-pages")
        self.assertNotEqual(guard_at, -1, "the Pages workflow does not call the guard")
        self.assertNotEqual(deploy_at, -1, "the Pages workflow does not deploy at all")
        self.assertLess(guard_at, deploy_at, "the guard has to run before the deploy")


if __name__ == "__main__":
    unittest.main()
