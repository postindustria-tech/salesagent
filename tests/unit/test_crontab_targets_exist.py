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


def _load_sync_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("sync_all_tenants", repo_root() / "scripts/ops/sync_all_tenants.py")
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_scheduled_sync_posts_to_the_trigger_route() -> None:
    """The cron script's trigger path is the route the sync blueprint serves.

    On v2.0.0 the script posted to ``/api/v1/sync/...`` while the app served ``/api/sync``:
    the blueprint declared one prefix and ``create_app`` registered it under another. The
    prefix now lives on the blueprint alone, so a bare app registering it resolves the path.
    """
    from flask import Flask

    from src.admin.sync_api import sync_api

    app = Flask(__name__)
    app.register_blueprint(sync_api)
    path = _load_sync_script().SYNC_TRIGGER_PATH.format(tenant_id="t1")

    endpoint, args = app.url_map.bind("localhost").match(path, method="POST")

    assert (endpoint, args) == ("sync_api.trigger_sync", {"tenant_id": "t1"})


def test_scheduled_sync_sends_the_configured_key(monkeypatch) -> None:
    """The sync API checks ``SYNC_API_KEY`` before the stored key, so the job must send it.

    The job used to send the stored ``api_key`` row whatever was configured, so on a
    deployment that sets ``SYNC_API_KEY`` every scheduled trigger answered 401.
    """
    monkeypatch.setenv("SYNC_API_KEY", "sk_from_env")

    assert _load_sync_script().sync_api_key() == "sk_from_env"
