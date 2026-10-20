"""A file the seller's own host cannot read or write is a deployment fault, on every path.

The googleads client writes its WSDL cache under ``~``. In an image whose runtime user has
no home directory that write raises the builtin ``PermissionError``, whose type and message
both say "permission", so it was reported as PERMISSION_DENIED: the buyer was told to check
their access and the operator went looking at GAM roles. It is the seller's deployment to
fix, which is CONFIGURATION_ERROR.

Two classifiers see such an error: ``map_gam_exception`` for the GAM calls wrapped in
``with_retry``, and ``adcp_error_for`` at the transport boundary for everything else. Both
must agree, so both are graded with the same cases.
"""

from __future__ import annotations

import pytest

from src.adapters.gam.utils.error_handler import map_gam_exception
from src.core.exceptions import adcp_error_for

_CLASSIFIERS = [
    pytest.param(adcp_error_for, id="transport_boundary"),
    pytest.param(map_gam_exception, id="gam_adapter"),
]


@pytest.mark.parametrize("classify", _CLASSIFIERS)
def test_local_file_error_is_configuration_error(classify):
    fault = PermissionError(13, "Permission denied", "/home/app")

    error = classify(fault)

    assert error.error_code == "CONFIGURATION_ERROR"
    assert error.internal_detail is fault


@pytest.mark.parametrize("classify", _CLASSIFIERS)
def test_permission_error_naming_no_file_stays_permission_denied(classify):
    """The counter-case: an authorization refusal raised as PermissionError keeps its code."""
    error = classify(PermissionError("Principal 'p1' does not own media buy 'mb_1'."))

    assert error.error_code == "PERMISSION_DENIED"
