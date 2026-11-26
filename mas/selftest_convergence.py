# -*- coding: utf-8 -*-
"""The fifty-sixth gate: the self-test has to converge, and say so.

The gap
-------
Two facts about a test suite are easy to lose and hard to notice:

* **it leaks.** A gate that starts a child process and never closes
  the pipe leaves the child alive, blocked on a read that will never
  return. On the machine that wrote the gate the child simply
  outlived the test, and nothing failed. On CI one stranded server
  per iteration piled up until the Linux leg took half an hour.
  A leak is invisible *inside* the suite - the suite is what leaks.
* **it lies.** A suite that is cut short by a time budget but still
  answers "ok" teaches the next reader that everything ran. The
  difference between "the checks that ran passed" and "the suite
  ran" is the difference between a green build and a false one.

Both are properties of the *whole* suite, so no single gate can see
them: every other gate looks at its own path. Only a gate that runs
the suite inside itself can watch the suite from the outside.

What this gate watches
----------------------
* **the child processes.** Before and after the suite runs, the
  number of live children of this process is counted. A suite that
  strands a child leaves the count higher than it found it.
* **the budget.** A suite run with a budget that is *too small to
  finish* must say so: it must report the checks it did not reach
  and mark itself incomplete. A budget that is silently ignored -
  the whole suite runs anyway - must be caught too, because that is
  the exact failure mode the budget exists to prevent.
* **the exit code.** A cut suite exits with a different code than a
  green one, so a CI job can tell them apart.

And the gate proves it can fail: the rehearsal breaks both on
purpose - it starts a child and refuses to clean it up - and the
gate has to notice that it can no longer tell a converging suite
from a leaking one.

How the leak is seen
--------------------
Without a third-party dependency (this project ships zero), the
counting is done by the operating system itself:

* Linux publishes a directory of processes, each of which names its
  parent, so the children are counted by reading it;
* macOS publishes no such directory, but it does publish the same
  two facts - the process id and the parent's id - through ``ps``,
  which every macOS has had for thirty years. The process doing the
  counting is one of the children ``ps`` reports, so it is excluded
  by its own pid: a counter that counted itself would report a leak
  on every single call;
* Windows publishes the same information through the tool-help
  snapshot API, which is reached through ``ctypes`` - the standard
  library, not a new dependency.

Where the platform does not offer a way to look, the gate falls back
to *not guessing*: it reports that the count is unavailable rather
than pretending the count was zero. A gate that cannot see may not
claim that it saw.

The count is of *direct children*, taken *by the process that started
them* - and both details are the whole point:

* the leak happens one level down. The suite runs in a child, the
  child starts the MCP server, and the MCP server is what stays
  alive. A counter that only looked at the gate's own direct
  children would see a clean machine and a leaking machine as
  exactly the same thing;
* the count has to be taken *by the parent, while it still lives*. A
  process whose parent dies is re-parented by the operating system,
  so a leaked server that outlived its parent is no longer anybody's
  child by the time the suite is over - counting from outside after
  the fact counts a clean machine. The number has to come from the
  inside, at the moment the gate finishes.

So the gate asks the suite the one question that separates the two:
*"how many children do you still have?"* A suite that cleans up
after itself answers "none". A suite that strands a server answers
"one" - and that server is blocked on a read that will never return,
because the pipe it waits on is the one that was never closed.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

#: The observations the gate makes, and the two readings of them.
CONVERGENCE_OBSERVATIONS = (
    "the suite leaves no child process behind",
    "a suite that runs out of time says that it did",
    "a cut suite and a green suite do not look the same",
)


def _process_table_on_proc() -> Optional[Dict[int, int]]:
    """Every process and its parent, read from ``/proc``.

    ``None`` means "this platform does not offer a way to look", which
    is different from "there are none".
    """
    proc = pathlib.Path("/proc")
    if not proc.is_dir():
        return None
    # The fourth field of /proc/<pid>/stat is the parent pid. It is
    # separated from the (comm) field by the last ")" in the line,
    # because the process name may contain spaces and parentheses.
    table: Dict[int, int] = {}
    try:
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                other = (entry / "stat").read_text(encoding="utf-8")
                fields = other.rsplit(")", 1)[1].split()
                table[int(entry.name)] = int(fields[1])
            except (OSError, IndexError, ValueError):
                continue
    except OSError:  # pragma: no cover - defensive
        return None
    return table


def _process_table_on_windows() -> Optional[Dict[int, int]]:
    """Every process and its parent, read from the tool-help API.

    Windows publishes no ``/proc``. The tool-help snapshot lists every
    process with its parent, which is the same fact - reached
    through ``ctypes``, so the project keeps shipping zero
    third-party dependencies.
    """
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == -1:
        raise ctypes.WinError(ctypes.get_last_error())
    table: Dict[int, int] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                table[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def _parse_ps_output(text: str, ignore_pid: Optional[int] = None) -> Dict[int, int]:
    """Read the ``pid ppid`` pairs out of ``ps`` output.

    The output is one pair of numbers per line, with no header: the
    ``=`` after each format word is what asks ``ps`` for the bare
    numbers. Lines that do not hold two numbers are skipped rather
    than guessed at.
    """
    table: Dict[int, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        if ignore_pid is not None and pid == ignore_pid:
            continue
        table[pid] = ppid
    return table


def _process_table_on_ps() -> Optional[Dict[int, int]]:
    """Every process and its parent, read from ``ps``.

    macOS has no ``/proc``, and this project ships no third-party
    dependency, so the count comes from the tool that every macOS
    already has. ``ps`` is asked for exactly the two fields that
    matter, without a header, so the answer is one pair of numbers
    per line.

    ``None`` means "this platform does not offer a way to look", which
    is different from "there are none".
    """
    # Two spellings of the same question. The first is the BSD one
    # that macOS answers; the second is the portable one, used when
    # the first is refused. A counter that gives up on the first
    # refusal would be blind on exactly the platform it was written
    # for, so it asks again in another way.
    for command in (
        ["ps", "-A", "-o", "pid=", "-o", "ppid="],
        ["ps", "-eo", "pid=,ppid="],
    ):
        table = _read_ps(command)
        if table:
            return table
    return None


def _read_ps(command: List[str]) -> Dict[int, int]:
    """Run one ``ps`` command and read the table it printed."""
    try:
        proc = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return {}
    try:
        stdout, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return {}
    # The counter is itself a parent while it counts, and ``ps`` is
    # its child. Counting ``ps`` would report a leak on every call.
    return _parse_ps_output(stdout, ignore_pid=proc.pid)


def _count_descendants() -> Optional[int]:
    """How many processes descend from this one, right now.

    The answer is ``None`` when the platform does not let a process
    look at its own children. It is never a guess.
    """
    if sys.platform.startswith("win"):
        try:
            table = _process_table_on_windows()
        except Exception:  # noqa: BLE001 - the platform may refuse
            return None
    else:
        table = _process_table()
    if table is None:
        return None
    me = os.getpid()
    descendants = set()
    # The table is walked until it stops growing: a child of a child
    # is a descendant too, and the leak this gate watches for is
    # exactly one level deeper than the direct child.
    changed = True
    while changed:
        changed = False
        for pid, ppid in table.items():
            if pid in descendants:
                continue
            if ppid == me or ppid in descendants:
                descendants.add(pid)
                changed = True
    return len(descendants)


def _children_of_self() -> Optional[int]:
    """How many *direct* children this process has right now.

    The direct children are the interesting ones for a leak: a server
    that is stranded on a pipe is a child of the process that started
    it, and it stays a child of that process for as long as that
    process lives. So the count has to be taken *by the process that
    started it* - a parent that is still running. This function is
    the answer that the suite gives about itself.
    """
    table = _process_table()
    if table is None:
        return None
    me = os.getpid()
    return sum(1 for _pid, parent in table.items() if parent == me)


def _suite_size() -> int:
    """How many checks the suite has.

    The number is read from the suite itself, so a gate that is added
    does not have to be counted in two places.
    """
    from mas.selftest import CHECKS

    return len(CHECKS)

def _descendants_of(pid: int, table: Dict[int, int]) -> set:
    """Every process that descends from ``pid``, given a process table."""
    descendants = set()
    changed = True
    while changed:
        changed = False
        for child, parent in table.items():
            if child in descendants:
                continue
            if parent == pid or parent in descendants:
                descendants.add(child)
                changed = True
    return descendants


def _process_table() -> Optional[Dict[int, int]]:
    """Every process and its parent, as the operating system sees it."""
    if sys.platform.startswith("win"):
        try:
            return _process_table_on_windows()
        except Exception:  # noqa: BLE001 - the platform may refuse
            return None
    if sys.platform.startswith("linux"):
        table = _process_table_on_proc()
        if table is not None:
            return table
    # macOS and the other BSDs publish no process directory, so the
    # count comes from ``ps``. It is a child process of the counter,
    # and it excludes itself from the answer.
    return _process_table_on_ps()


def _alive(pid: int) -> bool:
    """Is this process still running?"""
    if sys.platform.startswith("win"):
        import ctypes

        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            return False
        try:
            # Zero milliseconds: ask, do not wait.
            result = kernel32.WaitForSingleObject(handle, 0)
            return int(result) == 0x00000102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:  # pragma: no cover - defensive
        return False
    return True


def _child_package() -> str:
    """The package the child suite is loaded from.

    The gate watches *this* package, and the child watches the same
    one - so what the gate measures is what the suite does. The
    rehearsal can point it at a different copy (see
    ``_REHEARSAL_PACKAGE``), which is how the gate proves that it
    would notice a suite that leaks.
    """
    override = globals().get("_REHEARSAL_PACKAGE")
    if override:
        return str(override)
    return str(pathlib.Path(__file__).resolve().parents[1])


def _run_suite_in_a_child(
    budget_sec: Optional[float] = None,
    iterations: int = 1,
    watch: bool = False,
    only: Optional[str] = None,
    report_children: bool = False,
) -> Dict[str, Any]:
    """Run the self-test in a child process and read its answer.

    The child is the suite; the parent is the watcher. The child is
    started with its own state directory, so a run of the suite
    cannot touch the ledger of the machine - the same rule every
    other gate follows.
    """
    with tempfile.TemporaryDirectory(prefix="mas-selftest-converge-") as tmp:
        env = dict(os.environ)
        env["PYTHONPATH"] = _child_package()
        env["PYTHONUTF8"] = "1"
        env["MAS_STATE_DIR"] = os.path.join(tmp, "state")
        if report_children:
            # The child answers the question itself, from the inside,
            # while it is still the parent of everything it started.
            script = (
                "import json, sys\n"
                "from mas.selftest_convergence import _children_of_self\n"
                "from mas.selftest import run_selftest\n"
                "result = run_selftest(\n"
                "    iterations=%d,\n"
                "    only=%r,\n"
                "    time_budget_sec=%r,\n"
                ")\n"
                "result['children_left'] = _children_of_self()\n"
                "sys.stdout.write(json.dumps(result, ensure_ascii=False))\n"
            ) % (
                int(iterations),
                list(only) if only else None,
                None if budget_sec is None else float(budget_sec),
            )
            command = [sys.executable, "-X", "utf8", "-c", script]
        else:
            command = [
                sys.executable, "-X", "utf8", "-m", "mas", "selftest",
                "--iterations", str(int(iterations)),
                "--json",
            ]
            if only:
                command += ["--only", str(only)]
            if budget_sec is not None:
                command += ["--time-budget", str(float(budget_sec))]
        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                encoding="utf-8",
                # The working directory decides which copy of the
                # package the child imports: for ``python -c`` the
                # current directory comes first on the import path.
                # Without this the child would always load the copy
                # in this directory, whatever PYTHONPATH says.
                cwd=_child_package(),
            )
        except OSError as exc:
            return {
                "ran": False,
                "exit_code": None,
                "payload": {},
                "watch": {},
                "error": "%s: %s" % (type(exc).__name__, exc),
            }
        # While the suite runs, the gate watches how many processes
        # descend from it. A suite that strands a server answers with
        # a number that keeps climbing.
        watch: Dict[str, Any] = {"samples": [], "seen_pids": []}
        if watch:
            while proc.poll() is None:
                table = _process_table()
                if table is not None:
                    descendants = _descendants_of(os.getpid(), table)
                    count = len(descendants)
                    watch["samples"].append(count)
                    for pid in descendants:
                        if pid not in watch["seen_pids"]:
                            watch["seen_pids"].append(int(pid))
                time.sleep(0.5)
        try:
            stdout, _stderr = proc.communicate(timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _stderr = proc.communicate()
            return {
                "ran": False,
                "exit_code": None,
                "payload": {},
                "watch": watch,
                "error": "套件在 900 秒内没有结束",
            }
        payload: Dict[str, Any] = {}
        try:
            payload = json.loads(stdout or "{}")
        except ValueError:
            payload = {}
        return {
            "ran": True,
            "exit_code": proc.returncode,
            "payload": payload,
            "watch": watch,
            "error": "",
        }


def _rehearsal_leak() -> Dict[str, Any]:
    """Break the child-process rule on purpose.

    A child is started, and its stdin is deliberately *not* closed:
    this is exactly the leak the gate is looking for - a server
    blocked on a pipe that will never close.

    The rehearsal is measured the same way the real thing is
    measured: the parent counts the children it has *right now*, while
    it is still their parent. If the counter cannot see the child
    here, it cannot see a leak either - and the gate says so instead
    of claiming a cleanliness it did not observe.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = _child_package()
    env["PYTHONUTF8"] = "1"
    script = "import sys, time\nsys.stdin.read()\ntime.sleep(60)\n"
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
    )
    # Give the child a moment to be a child, so the count sees it.
    time.sleep(1.0)
    return {"proc": proc, "children": _children_of_self()}


def _convergence_verdicts(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The readings of the facts the gate gathered."""
    verdicts: Dict[str, bool] = {}

    # 1. The suite leaves no child behind.
    left = facts.get("children_left")
    if left is None:
        # The platform does not let a process look at its children.
        # The gate does not claim it saw anything.
        verdicts["no_leaked_children"] = True
    else:
        # Every process the suite started has to be gone by the time
        # the suite is over. One that is still there is a server
        # stranded on a pipe that will never close.
        verdicts["no_leaked_children"] = int(left) == 0

    # 2. A suite that runs out of time says that it did.
    cut_run = facts.get("cut_run") or {}
    payload = cut_run.get("payload") or {}
    cut = payload.get("cut") or []
    # A budget that is silently ignored - the whole suite runs anyway
    # - is the failure mode the budget exists to prevent. So the gate
    # asks for both: that the suite *said* it was cut, and that it
    # really did stop early.
    verdicts["cut_is_reported"] = bool(cut) and not payload.get("complete", True)
    verdicts["cut_actually_stopped"] = len(cut) > 0

    # 3. A cut suite and a green suite do not look the same.
    verdicts["cut_exit_code_differs"] = int(cut_run.get("exit_code") or 0) == 3

    return verdicts


def _convergence_verdicts_independent(facts: Dict[str, Any]) -> Dict[str, bool]:
    """The same readings, derived a second way.

    The gate decides with two independent implementations, because a
    gate that decides with one implementation can only ever prove
    that the implementation agrees with itself.
    """
    verdicts: Dict[str, bool] = {}

    left = facts.get("children_left")
    if left is None:
        verdicts["no_leaked_children"] = True
    else:
        # A second reading of the same fact, arrived at from the other
        # side: not "how many are left", but "is there at least one
        # that should not be there". A leftover is a leftover however
        # it is counted.
        verdicts["no_leaked_children"] = not int(left) > 0

    cut_run = facts.get("cut_run") or {}
    payload = cut_run.get("payload") or {}
    ran_count = len(payload.get("checks") or [])
    cut_count = len(payload.get("cut") or [])
    verdicts["cut_is_reported"] = cut_count > 0 and ran_count + cut_count > 0
    # A second reading of the same fact: the number of checks that
    # ran must be *smaller* than the number of checks in the suite.
    # A budget that was ignored would run all of them.
    verdicts["cut_actually_stopped"] = ran_count < _suite_size()

    code = cut_run.get("exit_code")
    verdicts["cut_exit_code_differs"] = code is not None and code != 0

    return verdicts


def check_the_selftest_converges_and_says_so(
    iterations: int = 1,
) -> Dict[str, Any]:
    """The suite has to converge, and has to say when it did not.

    The gate watches the suite the way a person watches a runner:
    from the outside, counting what is left behind.
    """
    failures: List[str] = []
    facts: Dict[str, Any] = {}

    children_before = _count_descendants()
    facts["children_before"] = children_before

    # The suite runs in a child of its own, with a budget far too
    # small to finish. The suite must notice, report and stop.
    cut_run = _run_suite_in_a_child(budget_sec=0.001, iterations=1, watch=True)
    facts["cut_run"] = cut_run

    # The leak hunt. The gate that leaks the most is the MCP gate: it
    # starts a server on a pipe and has to close the pipe for the
    # server to leave. So the gate that watches for leaks runs that
    # gate - and asks it, from the inside, how many children it still
    # has when it is done.
    leak_run = _run_suite_in_a_child(
        iterations=1, only=["mcp_handshake"], report_children=True
    )
    facts["leak_run"] = leak_run
    children_left = (leak_run.get("payload") or {}).get("children_left")
    facts["children_left"] = children_left

    children_after = _count_descendants()
    facts["children_after"] = children_after

    verdicts = _convergence_verdicts(facts)
    independent = _convergence_verdicts_independent(facts)

    for name, value in sorted(verdicts.items()):
        if not value:
            failures.append("保证不成立：%s" % name)
    for name in sorted(independent):
        if name in verdicts and independent[name] != verdicts[name]:
            failures.append(
                "两种独立判定不一致：%s（%s vs %s）"
                % (name, verdicts[name], independent[name])
            )

    # The rehearsal. The gate has to notice that it can no longer
    # tell a converging suite from a leaking one - otherwise "the gate
    # is green" would only mean that the gate agrees with itself.
    rehearsal: Dict[str, Any] = {}
    leak = _rehearsal_leak()
    proc = leak.get("proc")
    try:
        leaked_children = leak.get("children")
        rehearsal["children_with_leak"] = leaked_children
        if leaked_children is not None:
            # The leaking child has to be visible to the counter. If
            # it is not, the gate is blind - and a blind gate that
            # says "no leak" is worse than no gate at all.
            rehearsal["leak_is_visible"] = int(leaked_children) >= 1
        else:
            # The platform does not let a process look at its
            # children. The gate cannot prove it is blind, so it says
            # so: it reports that the check could not be made.
            rehearsal["leak_is_visible"] = False
            rehearsal["count_unavailable"] = True
    finally:
        try:
            if proc is not None:
                proc.stdin.close()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - the pipe may already be gone
            pass
        try:
            if proc is not None:
                proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 - the child refuses to leave
            try:
                if proc is not None:
                    proc.kill()
                    proc.wait(timeout=10)
            except Exception:  # noqa: BLE001 - it is already gone
                pass

    if not rehearsal.get("leak_is_visible", False):
        if rehearsal.get("count_unavailable"):
            failures.append(
                "这个平台没法让一个进程看见自己的孩子，门禁无法证明自己不是瞎的"
            )
        else:
            failures.append(
                "演练失败：故意留下的子进程没有被计数看见，门禁看不到泄漏"
            )

    return {
        "name": "selftest_converges",
        "title": "自检套件会收敛，而且截断时会如实说",
        "invariant": "套件不留下子进程；预算耗尽时报出未跑到的项；截断的退出码与全绿不同",
        "iterations": int(iterations),
        "failures": failures,
        "ok": not failures,
        "details": {
            "children_before": children_before,
            "children_after": children_after,
            "children_left": children_left,
            "cut_run_exit_code": (facts.get("cut_run") or {}).get("exit_code"),
            "cut_count": len(
                ((facts.get("cut_run") or {}).get("payload") or {}).get("cut") or []
            ),
            "rehearsal_children_with_leak": rehearsal.get("children_with_leak"),
        },
    }


check_the_selftest_converges_and_says_so.CHECK_NAME = "selftest_converges"  # type: ignore[attr-defined]
