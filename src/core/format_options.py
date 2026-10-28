"""The one bridge between this seller's named formats and AdCP 3.2's canonical format options.

Named format ids (``{agent_url, id}``) stay this seller's internal identity: the database,
the adapters' size and template mappings and the admin UI are all keyed on them. The wire is
canonical. AdCP 3.2 ``core/product.json`` ``format_options``: "New 3.2 products MUST carry
format_options; a seller MAY additionally project the same declarations to deprecated
format_ids for older 3.x peers", and ``reference/migration/3-1-to-3-2.mdx`` upgrade step 8:
"Adopt canonical format capability/product discovery and isolate legacy projection at
compatibility boundaries". This module is that projection, in both directions, so no tool
spells it.

The SDK does the mapping (``adcp.canonical_formats.projection``): its RC3 reference catalog
and the ``registries/v1-canonical-mapping.json`` registry turn a named format into a
canonical declaration offline, and the declaration's ``format_option_id`` is the normative
``migrated_<hash>`` of the named tuple, which is what lets a canonical reference come back
to the named format it was projected from.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from adcp.canonical_formats.projection import migrated_format_option_id, project_legacy_format_id
from adcp.types import Format as LibraryFormatDeclaration
from adcp.types.legacy import LegacyFormatId

logger = logging.getLogger(__name__)

__all__ = ["format_options_for", "named_format_for_option"]

NamedFormat = LegacyFormatId | Mapping[str, Any]


def format_options_for(named_formats: Iterable[NamedFormat], *, product_id: str) -> list[LibraryFormatDeclaration]:
    """The canonical declarations for *named_formats*, in order, without duplicates.

    A named format the SDK cannot map is left out and logged: the product still accepts
    every format that did map, and inventing a canonical shape for one that did not would
    advertise a contract nothing grades. An empty result means the product has no canonical
    format and cannot be offered on a 3.2 wire, which the caller decides.
    """
    declarations: list[LibraryFormatDeclaration] = []
    seen: set[str] = set()
    for index, named in enumerate(named_formats):
        projected = project_legacy_format_id(named, product_id=product_id, field=f"format_ids[{index}]")
        if projected.declaration is None:
            logger.warning(
                "Product %s: named format %r has no canonical projection (%s)",
                product_id,
                _identifier(named),
                projected.diagnostic.resolution_failure if projected.diagnostic else "unknown",
            )
            continue
        option_id = projected.declaration.format_option_id
        if option_id is not None and option_id in seen:
            continue
        if option_id is not None:
            seen.add(option_id)
        declarations.append(projected.declaration)
    return declarations


def named_format_for_option(format_option_id: str, named_formats: Sequence[NamedFormat]) -> NamedFormat | None:
    """The named format whose projection carries *format_option_id*, or None.

    The reverse of :func:`format_options_for`, by the SDK's deterministic id rather than by
    re-projecting: ``migrated_format_option_id`` is a pure function of the named tuple.
    """
    for named in named_formats:
        if migrated_format_option_id(named) == format_option_id:
            return named
    return None


def _identifier(named: NamedFormat) -> object:
    return named.get("id") if isinstance(named, Mapping) else named.id
