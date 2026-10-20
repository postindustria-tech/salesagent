#!/usr/bin/env python3
"""
Sync all GAM-enabled tenants via the sync API.
This script is intended to be run as a cron job.
"""

import logging
import os
import sys

import requests

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.admin.auth_helpers import get_api_key_from_config
from src.admin.sync_api import (
    SYNC_API_KEY_CONFIG,
    SYNC_API_KEY_ENV,
    SYNC_API_PREFIX,
    initialize_tenant_management_api_key,
)
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, Tenant

logger = logging.getLogger(__name__)

#: The sync API's trigger route (same name as on main).
SYNC_TRIGGER_PATH = SYNC_API_PREFIX + "/trigger/{tenant_id}"


def sync_api_key() -> str:
    """The key the sync API accepts: the lookup its decorator does, env var before stored key.

    A key is minted and stored only when neither is configured.
    """
    return get_api_key_from_config(SYNC_API_KEY_ENV, SYNC_API_KEY_CONFIG) or initialize_tenant_management_api_key()


def gam_tenants_to_sync(session: Session) -> list[tuple[str, str]]:
    """(tenant_id, name) of every active GAM tenant with a network code and credentials.

    Credentials are an OAuth refresh token or a service-account key; requiring the
    refresh token alone skipped every service-account tenant.
    """
    stmt = (
        select(Tenant.tenant_id, Tenant.name)
        .join(AdapterConfig, Tenant.tenant_id == AdapterConfig.tenant_id)
        .where(
            Tenant.ad_server == "google_ad_manager",
            Tenant.is_active,
            AdapterConfig.gam_network_code.isnot(None),
            or_(
                AdapterConfig.gam_refresh_token.isnot(None),
                AdapterConfig._gam_service_account_json.isnot(None),
            ),
        )
    )
    return [(tenant_id, name) for tenant_id, name in session.execute(stmt).all()]


def sync_all_gam_tenants():
    """Sync all tenants that have Google Ad Manager configured."""
    api_key = sync_api_key()

    with get_db_session() as session:
        tenants = gam_tenants_to_sync(session)

    if not tenants:
        logger.info("No GAM tenants found to sync")
        return

    logger.info(f"Found {len(tenants)} GAM tenants to sync")

    # Sync each tenant
    for tenant_id, tenant_name in tenants:
        logger.info(f"Syncing tenant: {tenant_name} ({tenant_id})")

        try:
            # Call sync API
            response = requests.post(
                f"http://localhost:{os.environ.get('ADCP_SALES_PORT', 8080)}{SYNC_TRIGGER_PATH.format(tenant_id=tenant_id)}",
                headers={"X-API-Key": api_key},
                json={"sync_type": "full"},
                timeout=300,  # 5 minute timeout per tenant
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "completed":
                    logger.info(f"✓ Sync completed for {tenant_name}")
                    if "summary" in result:
                        summary = result["summary"]
                        logger.info(f"  - Ad units: {summary.get('ad_units', {}).get('total', 0)}")
                        logger.info(f"  - Targeting keys: {summary.get('custom_targeting', {}).get('total_keys', 0)}")
                else:
                    logger.warning(f"Sync status for {tenant_name}: {result.get('status')}")
            elif response.status_code == 409:
                logger.info(f"Sync already in progress for {tenant_name}")
            else:
                logger.error(f"Failed to sync {tenant_name}: HTTP {response.status_code}")

        except requests.exceptions.Timeout:
            logger.error(f"Sync timeout for {tenant_name}")
        except Exception as e:
            logger.error(f"Error syncing {tenant_name}: {e}")

    logger.info("Sync job completed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    logger.info("Starting scheduled sync job")
    sync_all_gam_tenants()
