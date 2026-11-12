"""``map_gam_exception`` keeps server-side file errors apart from GAM authorization errors."""

from src.adapters.gam.utils.error_handler import (
    GAMConfigurationError,
    GAMErrorType,
    GAMPermissionError,
    map_gam_exception,
)


def test_local_file_error_is_configuration_not_gam_permission():
    """The googleads cache write under a missing home raises the builtin PermissionError.

    Its type name and message both say "permission", so it was reported as "GAM permission
    denied" and sent operators to check GAM roles instead of the container.
    """
    error = map_gam_exception(PermissionError(13, "Permission denied", "/home/app"))

    assert isinstance(error, GAMConfigurationError)
    assert error.error_type == GAMErrorType.CONFIGURATION
    assert "not a GAM error" in str(error)


def test_gam_permission_fault_stays_permission():
    fault = type("GoogleAdsServerFault", (Exception,), {})("PermissionError.PERMISSION_DENIED on advertiser 12345")

    assert isinstance(map_gam_exception(fault), GAMPermissionError)
