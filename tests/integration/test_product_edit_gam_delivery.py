"""Editing a GAM product's pricing re-derives how it is delivered, and nothing else does.

``add_product`` derives ``delivery_type`` from the pricing (fixed rate is guaranteed,
auction is not) and seeds ``implementation_config`` from it. ``edit_product`` used to
re-derive only when the form sent an explicit ``line_item_type``, and no form does. A
product created as "CPM - Fixed Rate" and edited to "CPM - Auction" therefore kept
``delivery_type="guaranteed"`` and ``line_item_type="STANDARD"``; at order time
``select_line_item_type`` reads ``delivery_type`` and books a STANDARD line item for what
the seller now sells as a floor-price auction.

The counter-cases matter as much. Guaranteed-with-auction and non-guaranteed-with-fixed are
valid products (``orders.py`` books both), so an edit that leaves the pricing as it was --
a rename, or a re-save that lists the same options in another order -- must not move the
delivery type, and must not reset what the seller tuned.
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
_AD_UNIT_IDS = ["23312403859"]


def _seed_product(
    *,
    delivery_type: str,
    fixed_options: list[bool],
    ad_server: str = "google_ad_manager",
    priority: int | None = None,
) -> None:
    tenant = TenantFactory(tenant_id=_TENANT_ID, ad_server=ad_server)
    implementation_config = GAMProductConfigService.generate_default_config(delivery_type)
    implementation_config["targeted_ad_unit_ids"] = _AD_UNIT_IDS
    if priority is not None:
        implementation_config["priority"] = priority
    product = ProductFactory(
        tenant=tenant,
        product_id=_PRODUCT_ID,
        delivery_type=delivery_type,
        implementation_config=implementation_config,
    )
    for is_fixed in fixed_options:
        PricingOptionFactory(product=product, pricing_model="cpm", is_fixed=is_fixed)


def _pricing_fields(fixed_options: list[bool]) -> list[tuple[str, str]]:
    """The edit form's indexed pricing fields, in the order given."""
    fields: list[tuple[str, str]] = []
    for index, is_fixed in enumerate(fixed_options):
        fields.append((f"currency_{index}", "USD"))
        if is_fixed:
            fields += [(f"pricing_model_{index}", "cpm_fixed"), (f"rate_{index}", "5.00")]
        else:
            fields += [(f"pricing_model_{index}", "cpm_auction"), (f"floor_{index}", "5.00")]
    return fields


def _save(client, fixed_options: list[bool], name: str = "Homepage Top 728x90") -> None:
    form = MultiDict(
        [
            ("name", name),
            ("delivery_measurement_provider", "Google Ad Manager"),
            *_pricing_fields(fixed_options),
        ]
    )
    response = client.post(f"/tenant/{_TENANT_ID}/products/{_PRODUCT_ID}/edit", data=form)
    assert response.status_code == 302, response.get_data(as_text=True)[:500]


def _reload(env: IntegrationEnv) -> Product:
    env.get_session().expire_all()
    return env.get_one(Product, tenant_id=_TENANT_ID, product_id=_PRODUCT_ID)


class TestPricingChangeRederivesGamDelivery:
    def test_fixed_to_auction_becomes_price_priority(self, authenticated_admin_client):
        with IntegrationEnv() as env:
            _seed_product(delivery_type="guaranteed", fixed_options=[True])

            _save(authenticated_admin_client, [False])

            product = _reload(env)
            assert product.delivery_type == "non_guaranteed"
            assert product.implementation_config["line_item_type"] == "PRICE_PRIORITY"
            assert product.implementation_config["delivery_rate_type"] == "AS_FAST_AS_POSSIBLE"
            # Targeting is the seller's choice, not a pricing default: it survives.
            assert product.implementation_config["targeted_ad_unit_ids"] == _AD_UNIT_IDS

    def test_auction_to_fixed_becomes_standard(self, authenticated_admin_client):
        with IntegrationEnv() as env:
            _seed_product(delivery_type="non_guaranteed", fixed_options=[False])

            _save(authenticated_admin_client, [True])

            product = _reload(env)
            assert product.delivery_type == "guaranteed"
            assert product.implementation_config["line_item_type"] == "STANDARD"
            assert product.implementation_config["creative_rotation_type"] == "EVEN"
            assert product.implementation_config["targeted_ad_unit_ids"] == _AD_UNIT_IDS


class TestUnchangedPricingKeepsDelivery:
    def test_unchanged_pricing_keeps_tuned_priority(self, authenticated_admin_client):
        with IntegrationEnv() as env:
            _seed_product(delivery_type="non_guaranteed", fixed_options=[False], priority=12)

            _save(authenticated_admin_client, [False])

            product = _reload(env)
            assert product.implementation_config["priority"] == 12
            assert product.implementation_config["line_item_type"] == "PRICE_PRIORITY"

    def test_rename_keeps_guaranteed_auction_product(self, authenticated_admin_client):
        """Guaranteed with auction pricing is a valid product; renaming it must not flip it."""
        with IntegrationEnv() as env:
            _seed_product(delivery_type="guaranteed", fixed_options=[False])

            _save(authenticated_admin_client, [False], name="Renamed")

            product = _reload(env)
            assert product.name == "Renamed"
            assert product.delivery_type == "guaranteed"
            assert product.implementation_config["line_item_type"] == "STANDARD"

    def test_same_options_in_another_order_keep_delivery(self, authenticated_admin_client):
        """The option list the form shows has no fixed order; re-saving it must be a no-op."""
        with IntegrationEnv() as env:
            _seed_product(delivery_type="guaranteed", fixed_options=[True, False], priority=8)

            _save(authenticated_admin_client, [False, True])

            product = _reload(env)
            assert product.delivery_type == "guaranteed"
            assert product.implementation_config["line_item_type"] == "STANDARD"
            assert product.implementation_config["priority"] == 8


class TestNonGamEditLeavesDeliveryType:
    def test_pricing_edit_does_not_rederive_for_other_adapters(self, authenticated_admin_client):
        """Edit never changed ``delivery_type`` for other adapters, and pricing must not start to."""
        with IntegrationEnv() as env:
            _seed_product(delivery_type="guaranteed", fixed_options=[True], ad_server="mock")

            _save(authenticated_admin_client, [False])

            assert _reload(env).delivery_type == "guaranteed"
