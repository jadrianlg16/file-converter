"""Conversion engine package.

Importing this package triggers each format module to register its handlers
with the registry (import for side effects). The Flask app imports the helpers
re-exported here.
"""
from . import documents, images, data, ebooks, audio  # noqa: F401  (registration side effects)
from .registry import (  # noqa: F401
    FORMATS,
    Format,
    UnknownFormat,
    categories,
    get_converter,
    matrix,
    sources,
    targets_for,
)

__all__ = [
    "FORMATS",
    "Format",
    "UnknownFormat",
    "categories",
    "get_converter",
    "matrix",
    "sources",
    "targets_for",
]
