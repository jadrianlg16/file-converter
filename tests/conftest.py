"""Shared pytest helpers. Conversion tests should skip (not fail) when the
external engine they need isn't installed locally, so the suite is meaningful
both on a bare dev box and inside the full Docker image."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converters import engine  # noqa: E402


def requires(binary: str):
    """Decorator: skip a test unless `binary` is on PATH."""
    return pytest.mark.skipif(not engine.have(binary), reason=f"{binary} not installed")
