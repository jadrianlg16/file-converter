"""Shared pytest helpers. Conversion tests should skip (not fail) when the
external engine they need isn't installed locally, so the suite is meaningful
both on a bare dev box and inside the full Docker image."""
import atexit
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# web_app reads DATA_DIR at import time; give the test run its own scratch dir
# instead of writing into a real data directory.
if not os.environ.get("DATA_DIR"):
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="fc-test-data-")
    atexit.register(shutil.rmtree, os.environ["DATA_DIR"], ignore_errors=True)

from converters import engine


def requires(binary: str):
    """Decorator: skip a test unless `binary` is on PATH."""
    return pytest.mark.skipif(not engine.have(binary), reason=f"{binary} not installed")
