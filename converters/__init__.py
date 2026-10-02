"""Conversion engine package.

Importing this package triggers each format module to register its handlers
with the registry (import for side effects). The Flask app imports the helpers
re-exported here.
"""

from . import audio, data, documents, ebooks, images  # noqa: F401 - imported to register handlers
from .registry import (
    FORMATS,
    Format,
    UnknownFormat,
    get_converter,
    matrix,
    sources,
    targets_for,
)

__all__ = [
    "FORMATS",
    "Format",
    "UnknownFormat",
    "get_converter",
    "matrix",
    "sources",
    "targets_for",
]
