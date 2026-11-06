"""Deferred-build models serialize on every edge, without completing them all at import.

adcp builds its models lazily (``defer_build=True``). A class that has only ever been
validated as a NESTED field keeps a ``MockValSer`` placeholder, and pydantic-core reads
that placeholder directly when an instance of the class is serialized on its own, which
fails with "'MockValSer' object cannot be converted to 'SchemaSerializer'". The SDK's
``model_dump`` recovers from it; its ``model_dump_json`` does not (it catches ``TypeError``,
``to_json`` raises ``PydanticSerializationError``), and neither does ``pydantic_core.to_json``
(the engine's JSONB serializer).

Completing every reachable class at import used to cover this, at a cost of ~225 MiB
resident for 767 model rebuilds. These tests pin the lazy replacement: the edge builds
the classes it meets, and importing the schemas builds none of them.
"""

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest
from adcp.types.base import AdCPBaseModel
from pydantic import BaseModel

from src.core.database.database_session import _pydantic_json_serializer
from src.core.deferred_models import build_deferred_models_or_reraise
from src.core.schemas._base import WireSerializerMixin


class _Leaf(AdCPBaseModel):
    value: int


class _Root(AdCPBaseModel):
    leaf: _Leaf


def test_engine_json_serializer_builds_a_class_only_seen_nested():
    root = _Root(leaf={"value": 1})
    assert _Leaf.__pydantic_complete__ is False  # precondition: built only as a nested field

    assert json.loads(_pydantic_json_serializer({"leaf": root.leaf, "items": [root.leaf]})) == {
        "leaf": {"value": 1},
        "items": [{"value": 1}],
    }


class _WireLeaf(AdCPBaseModel):
    value: int


class _WireRoot(WireSerializerMixin, AdCPBaseModel):
    leaf: _WireLeaf


def test_wire_serializer_builds_a_nested_class_on_first_dump():
    # The SDK's model_dump recovers only from a bare TypeError; raised inside our wrap
    # serializer, pydantic re-raises it as PydanticSerializationError, so the wire seat
    # must build the classes itself. Every wire response passes through this serializer.
    root = _WireRoot(leaf={"value": 1})
    assert _WireLeaf.__pydantic_complete__ is False  # precondition: built only as a nested field

    assert root.model_dump(mode="json") == {"leaf": {"value": 1}}


def test_importing_schemas_completes_no_library_leaf_class():
    # A fresh interpreter: another test may already have built the class in this one.
    probe = (
        "import src.core.schemas\n"
        "from adcp.types import ReportingPeriod\n"
        "print(ReportingPeriod.__pydantic_complete__)\n"
    )
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=repo_root)
    assert result.stdout.strip().splitlines()[-1] == "False"


class _OwnHeldLeaf(AdCPBaseModel):
    value: int


class _OwnHeldRoot(AdCPBaseModel):
    leaf: _OwnHeldLeaf


class _OwnHolder(BaseModel):
    leaf: _OwnHeldLeaf


def test_engine_json_serializer_builds_a_class_held_by_a_non_adcp_model():
    leaf = _OwnHeldRoot(leaf={"value": 2}).leaf
    assert _OwnHeldLeaf.__pydantic_complete__ is False  # precondition: built only as a nested field

    holder = _OwnHolder.model_construct(leaf=leaf)
    assert json.loads(_pydantic_json_serializer({"own": holder})) == {"own": {"leaf": {"value": 2}}}


class _DataclassHeldLeaf(AdCPBaseModel):
    value: int


class _DataclassHeldRoot(AdCPBaseModel):
    leaf: _DataclassHeldLeaf


@dataclasses.dataclass
class _DataclassHolder:
    leaves: list[_DataclassHeldLeaf]


def test_engine_json_serializer_builds_a_class_held_by_a_dataclass():
    # The SDK's own walker does not descend into dataclasses; ours must.
    leaf = _DataclassHeldRoot(leaf={"value": 3}).leaf
    assert _DataclassHeldLeaf.__pydantic_complete__ is False  # precondition: built only as a nested field

    assert json.loads(_pydantic_json_serializer({"dataclass": _DataclassHolder(leaves=[leaf])})) == {
        "dataclass": {"leaves": [{"value": 3}]}
    }


def test_a_failure_other_than_a_deferred_class_is_reraised_unchanged():
    failure = TypeError("unrelated")
    with pytest.raises(TypeError) as raised:
        build_deferred_models_or_reraise({}, failure)
    assert raised.value is failure
