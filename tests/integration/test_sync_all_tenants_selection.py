"""The scheduled sync selects every active GAM tenant that can authenticate.

The job used to require ``gam_refresh_token``, so a tenant connected with a service account
-- which has no refresh token -- was never selected: the scheduled sync logged
"No GAM tenants found to sync" and did nothing, even with the crontab path fixed.
"""

import importlib.util

import pytest

from tests.factories import AdapterConfigFactory, TenantFactory
from tests.harness._base import IntegrationEnv
from tests.unit._architecture_helpers import repo_root

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _load_sync_script():
    spec = importlib.util.spec_from_file_location("sync_all_tenants", repo_root() / "scripts/ops/sync_all_tenants.py")
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def _gam_tenant(tenant_id: str, *, is_active: bool = True, **credentials) -> None:
    tenant = TenantFactory(tenant_id=tenant_id, ad_server="google_ad_manager", is_active=is_active)
    AdapterConfigFactory(tenant=tenant, adapter_type="google_ad_manager", gam_network_code="123456", **credentials)


def test_selects_oauth_and_service_account_tenants(integration_db):
    with IntegrationEnv() as env:
        _gam_tenant("oauth", gam_refresh_token="1//refresh-token")
        # The column holds the encrypted key; selection only asks whether one is stored.
        _gam_tenant("service_account", gam_auth_method="service_account", _gam_service_account_json="encrypted-key")
        _gam_tenant("no_credentials")
        _gam_tenant("inactive", is_active=False, gam_refresh_token="1//refresh-token")

        selected = _load_sync_script().gam_tenants_to_sync(env.get_session())

        assert {tenant_id for tenant_id, _name in selected} == {"oauth", "service_account"}
