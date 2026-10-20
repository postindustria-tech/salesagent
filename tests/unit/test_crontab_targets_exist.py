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


def test_scheduled_sync_posts_to_a_registered_sync_route() -> None:
    """The cron script imports and posts to a route the admin app serves.

    On v2.0.0 the script imported a renamed key helper (ImportError) and posted to
    ``/api/v1/sync/...`` while the blueprint is registered at ``/api/sync``.
    """
    import importlib.util
    from unittest.mock import MagicMock, patch
    from urllib.parse import urlsplit

    from src.admin.app import create_app

    spec = importlib.util.spec_from_file_location("sync_all_tenants", repo_root() / "scripts/ops/sync_all_tenants.py")
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    tenant = MagicMock(tenant_id="t1")
    session = MagicMock()
    session.query.return_value.join.return_value.filter.return_value.all.return_value = [(tenant, MagicMock())]
    with (
        patch.object(script, "initialize_tenant_management_api_key", return_value="sk_test"),
        patch.object(script, "get_db_session") as db,
        patch.object(script.requests, "post") as post,
    ):
        db.return_value.__enter__.return_value = session
        script.sync_all_gam_tenants()

    path = urlsplit(post.call_args.args[0]).path
    endpoint, args = create_app().url_map.bind("localhost").match(path, method="POST")
    assert endpoint == "sync_api.trigger_sync"
    assert args == {"tenant_id": "t1"}
