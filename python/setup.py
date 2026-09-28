"""The package version comes from ../include/prismlib.h (PRISMLIB_VERSION_*).
Everything else is declared in pyproject.toml."""
import re
from pathlib import Path

from setuptools import setup

_HEADER = Path(__file__).resolve().parent.parent / "include" / "prismlib.h"


def _version() -> str:
    text = _HEADER.read_text(encoding="utf-8")
    parts = []
    for name in ("MAJOR", "MINOR", "PATCH"):
        m = re.search(r"#define\s+PRISMLIB_VERSION_%s\s+(\d+)" % name, text)
        if not m:
            raise RuntimeError("PRISMLIB_VERSION_%s not found in %s" % (name, _HEADER))
        parts.append(m.group(1))
    return ".".join(parts)


setup(version=_version())
