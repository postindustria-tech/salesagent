"""Schema package — re-exports all names for backward compatibility.

``from src.core.schemas import Creative`` continues to work unchanged.
Creative-domain classes live in ``src.core.schemas.creative``;
product-domain classes in ``src.core.schemas.product``;
delivery-domain classes in ``src.core.schemas.delivery``;
everything else lives in ``src.core.schemas._base``.
"""

# isort: off
# Import order matters: product/delivery shadow _base duplicates, creative resolves forward refs.
# pricing exports the local pricing member subclasses (CpmPricingOption, ...) and their union;
# _base's legacy flat PricingOption keeps that one package-level name (pricing's __all__ omits it).
from src.core.schemas._base import *  # noqa: F401, F403
from src.core.schemas.notification import *  # noqa: F401, F403
from src.core.schemas.pricing import *  # noqa: F401, F403
from src.core.schemas._base import GetMediaBuysPackage as _GetMediaBuysPackage
from src.core.schemas._base import PackageRequest as _PackageRequest
from src.core.schemas.product import *  # noqa: F401,F403
from src.core.schemas.delivery import *  # noqa: F401,F403
from src.core.schemas.creative import *  # noqa: F401, F403
from src.core.schemas.account import *  # noqa: F401, F403
from src.core.schemas.creative import CreativeApproval as _CreativeApproval
from src.core.schemas.creative import CreativeAssetRequest as _CreativeAssetRequest
# isort: on

_PackageRequest.model_rebuild(_types_namespace={"CreativeAssetRequest": _CreativeAssetRequest})
_GetMediaBuysPackage.model_rebuild(_types_namespace={"CreativeApproval": _CreativeApproval})

# adcp 6.6 builds its models lazily (``defer_build=True``): a library class only ever
# validated as a nested field keeps pydantic's placeholder serializer until it is needed.
# Nothing here completes them eagerly. Completing the transitive closure of every schema
# at import (767 rebuilds) held ~225 MiB resident per process; instead each serialization
# edge builds the classes it actually meets: the SDK's ``model_dump``, the wire serializer
# (``WireSerializerMixin``) and the engine's JSONB serializer (``src/core/deferred_models.py``).
# Pinned by tests/unit/test_deferred_model_serialization.py.
