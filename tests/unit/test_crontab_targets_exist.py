"""The image's cron jobs run files the image contains.

Every compose file runs the image with ``SKIP_CRON``, so no suite executes the crontab;
this reads the file the image is built from. On v2.0.0 both jobs named
``/app/scripts/sync_all_tenants.py`` after the script moved to ``scripts/ops/``, so the
scheduled GAM sync never ran. Ported from upstream #2310.
"""

from __future__ import annotations

from tests.unit._architecture_helpers import repo_root

_APP_DIR = "/app/"  # Dockerfile: WORKDIR /app + COPY . .


def test_every_crontab_target_exists_in_the_image() -> None:
    jobs = [
        line
        for line in (repo_root() / "crontab").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert jobs, "crontab has no jobs"
    targets = [token for job in jobs for token in job.split() if token.startswith(_APP_DIR)]
    assert targets, f"no crontab job names a file under {_APP_DIR}: {jobs}"
    missing = [t for t in targets if not (repo_root() / t.removeprefix(_APP_DIR)).is_file()]
    assert not missing, f"crontab runs files the image does not contain: {missing}"
