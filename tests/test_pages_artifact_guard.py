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
import base64
import json
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


def _runtime_token(run_id="run-123", job_id="job-456"):
    """Build an unsigned JWT that carries an Actions.Results scope."""

    def _part(payload):
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    scope = "Actions.ExampleScope"
    if run_id:
        scope += " Actions.Results:%s:%s" % (run_id, job_id)
    header = _part({"alg": "none", "typ": "JWT"})
    body = _part({"scp": scope})
    return "%s.%s." % (header, body)


def _deploy_job_steps():
    """Return the text of the deploy job, as one block."""
    text = (REPO / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.rstrip() == "  deploy:":
            start = index
            break
    if start is None:
        return ""
    block = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        block.append(line)
    return "\n".join(block)


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
        block = _deploy_job_steps()
        guard_at = -1
        deploy_at = -1
        for index, line in enumerate(block.splitlines()):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "pages_artifact_guard.py" in stripped:
                guard_at = index
            if stripped.startswith("uses: actions/deploy-pages"):
                deploy_at = index
        self.assertNotEqual(guard_at, -1, "the Pages workflow does not call the guard")
        self.assertNotEqual(deploy_at, -1, "the Pages workflow does not deploy at all")
        self.assertLess(guard_at, deploy_at, "the guard has to run before the deploy")

    def test_the_upload_and_the_deployment_are_separate_jobs(self):
        # One job means the deployment looks the artifact up
        # milliseconds after it was finalized, and the lookup comes
        # back empty - actions/deploy-pages#451. Two jobs put seconds
        # between them.
        text = (REPO / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        self.assertIn("needs: build", text, "the deployment does not wait for the upload")
        upload_at = text.find("actions/upload-pages-artifact")
        needs_at = text.find("needs: build")
        self.assertLess(upload_at, needs_at, "the upload has to happen in the first job")


class TheGuardSpeaksTheDeployStepsLanguage(unittest.TestCase):
    """The guard has to watch the index the deploy step actually reads.

    The deploy step does not use the public REST API - it uses the
    internal results service through a Twirp call. The two indexes are
    not the same, and a guard that watches only the REST one reports
    "one artifact, all good" while the deploy step still finds none.
    """

    def test_the_runtime_variables_are_preferred_over_the_rest_api(self):
        calls = []

        def fake_twirp(url, token, run_id, job_id):
            calls.append(("twirp", url, run_id, job_id))
            return [{"id": 1, "name": "github-pages", "size": 10}]

        def fake_rest(repo, run_id, token=None):
            calls.append(("rest", repo, run_id))
            return [{"id": 2, "name": "github-pages", "size": 10}]

        env = {
            "ACTIONS_RUNTIME_TOKEN": _runtime_token("run-id", "job-id"),
            "ACTIONS_RESULTS_URL": "https://results.example/",
        }
        with (
            mock.patch.dict("os.environ", env, clear=False),
            mock.patch.object(guard_module, "_twirp_list", fake_twirp),
            mock.patch.object(guard_module, "_rest_list", fake_rest),
        ):
            listing = guard_module.list_artifacts("owner/name", "123")

        self.assertEqual(calls[0][0], "twirp", "the deploy step's own index has to be used")
        self.assertEqual(listing[0]["id"], 1)

    def test_outside_a_runner_the_rest_api_is_the_fallback(self):
        env = {"ACTIONS_RUNTIME_TOKEN": "", "ACTIONS_RESULTS_URL": ""}
        calls = []

        def fake_rest(repo, run_id, token=None):
            calls.append(repo)
            return [{"id": 3, "name": "github-pages", "size": 10}]

        with (
            mock.patch.dict("os.environ", env, clear=False),
            mock.patch.object(guard_module, "_rest_list", fake_rest),
        ):
            listing = guard_module.list_artifacts("owner/name", "123")

        self.assertEqual(calls, ["owner/name"])
        self.assertEqual(listing[0]["id"], 3)

    def test_the_twirp_request_carries_the_backend_ids(self):
        captured = {}

        class _Response:
            def read(self):
                return json.dumps(
                    {"artifacts": [{"databaseId": "42", "name": "github-pages", "size": "7"}]}
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = request.data.decode("utf-8")
            captured["headers"] = request.headers
            return _Response()

        with mock.patch.object(guard_module.urllib.request, "urlopen", fake_urlopen):
            listing = guard_module._twirp_list(
                "https://results.example/", "token-value", "run-id", "job-id"
            )

        self.assertIn("/twirp/%s/%s" % (guard_module.TWIRP_SERVICE, guard_module.TWIRP_METHOD), captured["url"])
        body = json.loads(captured["body"])
        self.assertEqual(body["workflow_run_backend_id"], "run-id")
        self.assertEqual(body["workflow_job_run_backend_id"], "job-id")
        self.assertEqual(listing, [{"id": 42, "name": "github-pages", "size": 7}])


class TheBackendIdsAreReadFromTheRuntimeToken(unittest.TestCase):
    def test_the_actions_results_scope_is_decoded(self):
        token = _runtime_token("run-123", "job-456")
        self.assertEqual(guard_module.backend_ids(token), ("run-123", "job-456"))

    def test_a_token_without_the_scope_is_reported(self):
        token = _runtime_token("", "")
        with self.assertRaises(guard_module.GuardError):
            guard_module.backend_ids(token)


if __name__ == "__main__":
    unittest.main()
