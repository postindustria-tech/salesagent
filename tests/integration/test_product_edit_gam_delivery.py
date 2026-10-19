"""Editing a GAM product's pricing re-derives how it is delivered.

``add_product`` derives ``delivery_type`` from the first pricing option (fixed rate is
guaranteed, auction is not) and seeds ``implementation_config`` from it. ``edit_product``
used to re-derive only when the form sent an explicit ``line_item_type``, and no form
does. A product created as "CPM - Fixed Rate" and edited to "CPM - Auction" therefore kept
``delivery_type="guaranteed"`` and ``line_item_type="STANDARD"``; at order time
``select_line_item_type`` reads ``delivery_type`` and books a STANDARD line item for what
the seller now sells as a floor-price auction.
"""

import pytest
from werkzeug.datastructures import MultiDict

from src.core.database.models import Product
from src.services.gam_product_config_service import GAMProductConfigService
from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "gam_edit_tenant"
_PRODUCT_ID = "gam_edit_product"


def _seed_gam_product(*, is_fixed: bool) -> None:
    delivery_type = "guaranteed" if is_fixed else "non_guaranteed"
    tenant = TenantFactory(tenant_id=_TENANT_ID, ad_server="google_ad_manager")
    implementation_config = GAMProductConfigService.generate_default_config(delivery_type)
    implementation_config["targeted_ad_unit_ids"] = ["23312403859"]
    product = ProductFactory(
        tenant=tenant,
        product_id=_PRODUCT_ID,
        delivery_type=delivery_type,
        implementation_config=implementation_config,
    )
    PricingOptionFactory(product=product, pricing_model="cpm", is_fixed=is_fixed)


def _edit_pricing(client, pricing_option: list[tuple[str, str]]) -> None:
    form = MultiDict(
        [
            ("name", "Homepage Top 728x90"),
            ("delivery_measurement_provider", "Google Ad Manager"),
            ("currency_0", "USD"),
            *pricing_option,
        ]
    )
    response = client.post(f"/tenant/{_TENANT_ID}/products/{_PRODUCT_ID}/edit", data=form)
    assert response.status_code == 302, response.get_data(as_text=True)[:500]


class TestEditingPricingRederivesGamDelivery:
    def test_fixed_to_auction_becomes_price_priority(self, authenticated_admin_client):
        with IntegrationEnv() as env:
            _seed_gam_product(is_fixed=True)

            _edit_pricing(authenticated_admin_client, [("pricing_model_0", "cpm_auction"), ("floor_0", "5.00")])

            env.get_session().expire_all()
            product = env.get_one(Product, tenant_id=_TENANT_ID, product_id=_PRODUCT_ID)
            assert product.delivery_type == "non_guaranteed"
            assert product.implementation_config["line_item_type"] == "PRICE_PRIORITY"
            assert product.implementation_config["delivery_rate_type"] == "AS_FAST_AS_POSSIBLE"
            # Targeting is the seller's choice, not a pricing default: it survives.
            assert product.implementation_config["targeted_ad_unit_ids"] == ["23312403859"]

    def test_auction_to_fixed_becomes_standard(self, authenticated_admin_client):
        with IntegrationEnv() as env:
            _seed_gam_product(is_fixed=False)

            _edit_pricing(authenticated_admin_client, [("pricing_model_0", "cpm_fixed"), ("rate_0", "5.00")])

            env.get_session().expire_all()
            product = env.get_one(Product, tenant_id=_TENANT_ID, product_id=_PRODUCT_ID)
            assert product.delivery_type == "guaranteed"
            assert product.implementation_config["line_item_type"] == "STANDARD"
            assert product.implementation_config["targeted_ad_unit_ids"] == ["23312403859"]

    def test_unchanged_pricing_keeps_configured_priority(self, authenticated_admin_client):
        """Re-saving with the same pricing model must not reset what the seller tuned."""
        with IntegrationEnv() as env:
            _seed_gam_product(is_fixed=False)
            product = env.get_one(Product, tenant_id=_TENANT_ID, product_id=_PRODUCT_ID)
            product.implementation_config = {**product.implementation_config, "priority": 12}
            env.get_session().commit()

            _edit_pricing(authenticated_admin_client, [("pricing_model_0", "cpm_auction"), ("floor_0", "5.00")])

            env.get_session().expire_all()
            product = env.get_one(Product, tenant_id=_TENANT_ID, product_id=_PRODUCT_ID)
            assert product.implementation_config["priority"] == 12
            assert product.implementation_config["line_item_type"] == "PRICE_PRIORITY"
