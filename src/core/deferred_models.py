"""Build adcp's deferred model classes on first serialization, not at import.

adcp declares its models with ``defer_build=True``. A class that has only ever been
validated as a nested field keeps pydantic's placeholder serializer (``MockValSer``), and
pydantic-core cannot use it when it serializes an instance through that class's own
serializer. Completing every reachable class up front costs ~225 MiB resident per process,
so each serialization edge that can meet such an instance catches that one failure, builds
the classes present in the value, and retries.

The edges that need this are the ones the SDK's own recovery does not cover:

- ``WireSerializerMixin._serialize_wire`` (``src/core/schemas/_base.py``): inside a wrap
  serializer the failure must be handled before pydantic re-wraps it;
- the engine's JSONB serializer (``src/core/database/database_session.py``), which calls
  ``pydantic_core.to_json`` directly.

The SDK's ``AdCPBaseModel.model_dump`` recovers by itself. Its ``model_dump_json`` does
NOT: it catches ``TypeError``, but ``to_json`` raises ``PydanticSerializationError``.

``model_rebuild()`` is not thread-safe (pydantic deletes the class's validator before it
assigns the new one), and the server builds classes lazily under concurrent requests
(threaded admin server, background services, thread pools), so builds here are serialized
by one process-wide lock. The SDK's own recovery in ``model_dump`` does not take this lock.
"""

import dataclasses
import threading
from typing import Any

from pydantic import BaseModel

_BUILD_LOCK = threading.RLock()


def build_deferred_models_or_reraise(value: Any, exception: Exception) -> None:
    """Build every deferred model class in ``value``'s graph; re-raise any other failure."""
    if "MockValSer" not in str(exception):
        raise exception
    with _BUILD_LOCK:
        _build_deferred_classes(value, set())


def _build_deferred_classes(value: Any, seen: set[int]) -> None:
    """Complete each still-deferred model class met while walking ``value``.

    Walks pydantic models (declared fields and ``extra`` fields), dataclasses, lists,
    tuples, sets and dict values. ``seen`` holds the ids of visited containers, so shared
    and cyclic references are walked once.
    """
    if isinstance(value, (str, bytes, int, float, bool)) or value is None:
        return
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)

    if isinstance(value, BaseModel):
        model_class = type(value)
        if not model_class.__pydantic_complete__:
            model_class.model_rebuild(force=False)
        children: Any = list(value.__dict__.values())
        if value.__pydantic_extra__:
            children.extend(value.__pydantic_extra__.values())
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        children = vars(value).values()
    elif isinstance(value, dict):
        children = value.values()
    elif isinstance(value, (list, tuple, set, frozenset)):
        children = value
    else:
        return

    for child in children:
        _build_deferred_classes(child, seen)
