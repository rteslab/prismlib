from .prismlib_c import prismlib, prismlibError, lib_version as version

from .constants import (
    LedId, SampleRate, ScanOptions, ScanStatus,
    SR_HZ, SR_NAME, sr_hz, sr_name, sr_supported,
    RESULT_SUCCESS, RESULT_BAD_PARAMETER, RESULT_BUSY,
    RESULT_TIMEOUT, RESULT_RESOURCE_UNAVAIL, RESULT_COMMS_FAILURE,
)

#: Version of the installed package.  version() reports the loaded C library.
try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("prismlib")
except Exception:          # running from the source tree, not installed
    __version__ = version()

__all__ = [
    "prismlib", "prismlibError", "version", "__version__",
    "LedId", "SampleRate", "ScanOptions", "ScanStatus",
    "SR_HZ", "SR_NAME", "sr_hz", "sr_name", "sr_supported",
    "RESULT_SUCCESS", "RESULT_BAD_PARAMETER", "RESULT_BUSY",
    "RESULT_TIMEOUT", "RESULT_RESOURCE_UNAVAIL", "RESULT_COMMS_FAILURE",
]
