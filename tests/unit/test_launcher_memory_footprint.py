"""The container launcher supervises child processes; it must not hold the schema set itself.

``scripts/deploy/run_all_services.py`` is the image's ENTRYPOINT and lives as long as the
container. Whatever it imports stays resident for that whole time. Importing
``src.core.database.models`` pulls in ``src.core.schemas`` and the generated ``adcp`` models,
several hundred megabytes that only the app server needs. Database initialization therefore
runs in a child process, the way migrations already do.

Each check runs the launcher in a fresh interpreter: importing it installs signal handlers,
and ``sys.modules`` of the test process says nothing about what the launcher loads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from tests.unit._architecture_helpers import repo_root

# Modules that build the generated adcp model classes. None may be resident in the launcher.
_HEAVY_MODULES = ("src.core.database.models", "src.core.database.database", "src.core.schemas")

# ``adcp.types`` is lazy: a flat ``from adcp.types import X`` builds the whole generated type
# graph, an import by schema path under ``adcp.types.domains`` does not. Measured on this tree:
# the launcher holds about 170 MB without the graph and above 530 MB with it.
_MAX_LAUNCHER_RSS_MB = 300

_PROBE = """
import ast, importlib, json, subprocess, sys
from pathlib import Path

child_exit_code = int(sys.argv[1])
launcher_path = Path("scripts/deploy/run_all_services.py")

import scripts.deploy.run_all_services as launcher

# The launcher reads the settings first; what that loads stays resident too.
launcher.validate_required_env()

# Every module the launcher names, at module level or inside a function, is loaded in-process
# at some point of a normal start.
for node in ast.walk(ast.parse(launcher_path.read_text(encoding="utf-8"))):
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        importlib.import_module(node.module)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            importlib.import_module(alias.name)

commands = []


def record_child(command, **kwargs):
    commands.append([str(part) for part in command])
    return subprocess.CompletedProcess(command, child_exit_code)


launcher.subprocess.run = record_child
try:
    launcher.init_database()
    launcher_exit_code = 0
except SystemExit as stop:
    launcher_exit_code = stop.code

heavy = sorted(name for name in sys.modules if name in set(sys.argv[2:]))
# Resident size now. Linux carries the parent's peak (ru_maxrss) across fork and exec, so there
# the probe reads its current size from /proc; elsewhere the peak is this process's own.
status = Path("/proc/self/status")
if status.exists():
    rss_kb = int(next(line for line in status.read_text().splitlines() if line.startswith("VmRSS:")).split()[1])
    rss_mb = rss_kb // 1024
else:
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = peak // (1024 * 1024 if sys.platform == "darwin" else 1024)
report = {"commands": commands, "exit": launcher_exit_code, "heavy": heavy, "rss_mb": rss_mb}
print("PROBE " + json.dumps(report))
"""


def _run_launcher_probe(child_exit_code: int) -> dict:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(child_exit_code), *_HEAVY_MODULES],
        cwd=repo_root(),
        env={**os.environ, "DATABASE_URL": "postgresql://launcher:probe@localhost:1/none", "ENCRYPTION_KEY": "probe"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    report_lines = [line for line in result.stdout.splitlines() if line.startswith("PROBE ")]
    assert report_lines, f"launcher probe did not report:\n{result.stdout}\n{result.stderr}"
    return json.loads(report_lines[-1].removeprefix("PROBE "))


def test_launcher_process_does_not_load_the_schema_set() -> None:
    report = _run_launcher_probe(child_exit_code=0)
    assert report["heavy"] == [], (
        f"the launcher process loaded {report['heavy']}; it keeps them for the container's lifetime. "
        "Run the work that needs them in a child process."
    )


def test_launcher_process_does_not_build_the_adcp_type_graph() -> None:
    report = _run_launcher_probe(child_exit_code=0)
    assert report["rss_mb"] <= _MAX_LAUNCHER_RSS_MB, (
        f"the launcher process holds {report['rss_mb']} MB. Something it loads, most likely on "
        "the settings path, imports a name from the flat `adcp.types` namespace; import it by its "
        "schema path under `adcp.types.domains` instead."
    )


def test_database_initialization_runs_in_a_child_python_process() -> None:
    report = _run_launcher_probe(child_exit_code=0)
    assert report["exit"] == 0
    assert len(report["commands"]) == 1, f"expected one child process, got {report['commands']}"
    command = report["commands"][0]
    assert command[0] == sys.executable
    assert command[1:] == ["-m", "src.core.database.database"], command


def test_failed_database_initialization_stops_the_launcher() -> None:
    report = _run_launcher_probe(child_exit_code=3)
    assert report["exit"] == 1, "a failed init_db child must stop the container start"


def test_database_module_initializes_when_run_as_a_script() -> None:
    """The child command above only works while the module keeps its ``__main__`` entry."""
    source = (repo_root() / "src" / "core" / "database" / "database.py").read_text(encoding="utf-8")
    main_guard = source.partition('if __name__ == "__main__":')[2]
    assert "init_db(exit_on_error=True)" in main_guard
