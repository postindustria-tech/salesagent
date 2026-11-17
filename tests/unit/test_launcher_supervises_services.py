"""The container launcher stops, with a failure code, when a service it runs fails.

``scripts/deploy/run_all_services.py`` is the image's ENTRYPOINT. It starts the app server,
nginx and the cron runner as child processes. A container whose app server has died still
looks "running" to the container runtime while nginx answers 502 for every request, so the
runtime's restart policy never fires. The launcher therefore watches its services: when one
exits on its own, or never starts, it stops the others and exits non-zero. A requested stop
(SIGTERM) still exits 0.

Each check runs the launcher's supervisor as a real process and reads its exit status,
because that status is what a container runtime acts on. The last check reads the launcher's
source to prove that ``main`` is wired to the supervisor.

These checks start processes and send signals, which ``tests/unit`` otherwise keeps out. They
are here because the subject is the container's entrypoint: no transport reaches it, so BDD
cannot grade it, and it needs no database or Docker stack, so the unit run is the one suite
that can execute it on every change.
"""

from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from tests.unit._architecture_helpers import repo_root

_LAUNCHER = "scripts/deploy/run_all_services.py"

# A service that stays up, one that already exited, one that ignores SIGTERM, and one whose
# output is not valid UTF-8.
_PROBE = """
import subprocess, sys

import scripts.deploy.run_all_services as launcher

scenario = sys.argv[1]
SLEEPER = "import time; time.sleep(60)"
GARBLER = "import sys; sys.stdout.buffer.write(b'bad ' + bytes([255]) + b' byte' + bytes([10]))"
launcher.handle_stop_signals(launcher.request_stop)
STUBBORN = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(60)"

if scenario == "stop_during_failure":
    launcher.STOP_GRACE_SECONDS = 3.0
    survivor = subprocess.Popen([sys.executable, "-c", STUBBORN], stdout=subprocess.PIPE)
    survivor.stdout.readline()  # its SIGTERM handler is installed
else:
    # Its own stdout: a survivor that outlives the probe must not hold the probe's pipe open.
    survivor = subprocess.Popen([sys.executable, "-c", SLEEPER], stdout=subprocess.DEVNULL)
launcher.register_service("survivor", survivor)
print(f"SURVIVOR {survivor.pid}", flush=True)

if scenario in ("service_dies", "stop_during_failure", "stop_after_death"):
    casualty = subprocess.Popen([sys.executable, "-c", f"import sys; sys.exit({sys.argv[2]})"])
    casualty.wait()
    launcher.register_service("casualty", casualty)
elif scenario == "service_never_starts":
    def missing_binary():
        raise FileNotFoundError("no such binary")
    launcher.start_service_thread("broken", missing_binary).join()
elif scenario == "all_running":
    garbler = subprocess.Popen([sys.executable, "-c", GARBLER], stdout=subprocess.PIPE)
    launcher.start_service_thread("garbler", lambda: launcher.relay_output("[garbler]", garbler)).join()

if scenario == "stop_after_death":
    # The stop signal lands after the service died and before the supervisor looked.
    launcher.request_stop()

if scenario == "all_running":
    launcher.supervise_services(seconds=0.3, poll_seconds=0.05)
    print("RETURNED", flush=True)
    survivor.kill()
else:
    print("SUPERVISING", flush=True)
    launcher.supervise_services(poll_seconds=0.05)
"""


class _Probe:
    """A running probe: its output so far, and the process it registered as a service."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.lines: list[str] = []
        self.survivor_pid: int | None = None

    def read_until(self, marker: str) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.append(line.rstrip("\n"))
            if line.startswith("SURVIVOR "):
                self.survivor_pid = int(line.split()[1])
            if marker in line:
                return
        raise AssertionError(f"the launcher probe ended before printing {marker!r}:\n" + "\n".join(self.lines))

    def finish(self) -> int:
        # The same reader as read_until: a second reader would miss what this one buffered.
        assert self.process.stdout is not None
        self.lines.extend(self.process.stdout.read().splitlines())
        return self.process.wait(timeout=60)

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def survivor_is_gone(self) -> bool:
        assert self.survivor_pid is not None
        try:
            os.kill(self.survivor_pid, 0)
        except ProcessLookupError:
            return True
        return False


@contextmanager
def _launcher_probe(scenario: str, casualty_exit_code: int = 3) -> Iterator[_Probe]:
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _PROBE, scenario, str(casualty_exit_code)],
        cwd=repo_root(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    probe = _Probe(process)
    # A launcher that fails to stop would block the reads below for ever; end it instead.
    watchdog = threading.Timer(60, process.kill)
    watchdog.start()
    try:
        yield probe
    finally:
        watchdog.cancel()
        # Whatever the check concluded, nothing it started may outlive it.
        for pid in (process.pid, probe.survivor_pid):
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    # Already gone; PermissionError means the number now names another user's process.
                    pass
        process.wait()
        assert process.stdout is not None
        process.stdout.close()


@pytest.mark.parametrize("service_exit_code", [3, 0])
def test_launcher_exits_with_a_failure_code_when_a_service_dies(service_exit_code: int) -> None:
    """A service that ends on its own is a failure whatever code it ended with."""
    with _launcher_probe("service_dies", service_exit_code) as probe:
        probe.read_until("SUPERVISING")
        assert probe.finish() == 1, f"a dead service must end the launcher with exit status 1:\n{probe.output}"
        assert f"casualty exited with code {service_exit_code}" in probe.output
        assert probe.survivor_is_gone(), "the remaining services must be stopped with the launcher"


def test_launcher_exits_with_a_failure_code_when_a_service_never_starts() -> None:
    with _launcher_probe("service_never_starts") as probe:
        probe.read_until("SUPERVISING")
        assert probe.finish() == 1, f"a service that never started must end the launcher:\n{probe.output}"
        assert "broken failed" in probe.output
        assert probe.survivor_is_gone()


def test_launcher_keeps_running_while_every_service_runs() -> None:
    with _launcher_probe("all_running") as probe:
        probe.read_until("RETURNED")
        assert probe.finish() == 0, f"the supervisor must not end the launcher while services run:\n{probe.output}"
        assert "[garbler] bad" in probe.output, "a line that is not valid UTF-8 must still be relayed"


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_requested_stop_ends_the_launcher_cleanly(stop_signal: signal.Signals) -> None:
    with _launcher_probe("requested_stop") as probe:
        probe.read_until("SUPERVISING")
        probe.process.send_signal(stop_signal)
        assert probe.finish() == 0, f"{stop_signal.name} is a requested stop, not a failure:\n{probe.output}"
        assert probe.survivor_is_gone()


def test_stop_signal_after_a_service_died_keeps_the_failure_code() -> None:
    """A stop that arrives before the supervisor has seen the dead service must not hide it."""
    with _launcher_probe("stop_after_death") as probe:
        probe.read_until("SUPERVISING")
        assert probe.finish() == 1, f"the failure must survive a stop signal:\n{probe.output}"
        assert "casualty exited with code 3" in probe.output
        assert probe.survivor_is_gone()


def test_stop_signal_during_a_failure_shutdown_keeps_the_failure_code() -> None:
    """A SIGTERM that arrives while the launcher stops after a failure must not report a clean stop."""
    with _launcher_probe("stop_during_failure") as probe:
        probe.read_until("casualty exited with code 3")
        probe.process.send_signal(signal.SIGTERM)
        assert probe.finish() == 1, f"the failure must survive a stop signal:\n{probe.output}"
        assert probe.survivor_is_gone(), "a service that ignores SIGTERM must be killed after the grace period"


def _calls(function: ast.FunctionDef) -> set[str]:
    return {ast.unparse(node.func) for node in ast.walk(function) if isinstance(node, ast.Call)}


def test_main_runs_every_service_under_the_supervisor() -> None:
    """The checks above grade the supervisor; this one proves the launcher uses it."""
    tree = ast.parse((repo_root() / _LAUNCHER).read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    unregistered = [
        name
        for name, function in functions.items()
        if "subprocess.Popen" in _calls(function) and "register_service" not in _calls(function)
    ]
    assert not unregistered, f"{unregistered} start a process the supervisor never sees"

    bare_threads = [
        name
        for name, function in functions.items()
        if "threading.Thread" in _calls(function) and name != "start_service_thread"
    ]
    assert not bare_threads, f"{bare_threads} start a service thread whose failure to start is not recorded"

    main = functions["main"]
    started = {
        ast.unparse(node.args[1])
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "start_service_thread"
    }
    assert started == {"run_mcp_server", "run_cron", "run_nginx"}

    assert "handle_stop_signals" in _calls(main), "main must hand stop signals to the supervisor"
    assert "time.sleep" not in _calls(main), "a wait in main must be a supervised one: supervise_services(seconds=...)"

    last = main.body[-1]
    assert isinstance(last, ast.Expr) and ast.unparse(last.value) == "supervise_services()", (
        "main must end by supervising the services until the launcher is stopped"
    )
