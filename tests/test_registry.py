"""Wiring tests for the registry + Flask layer. No external engines needed."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converters import FORMATS, get_converter, matrix, sources, targets_for
from converters.registry import CATEGORIES


def test_matrix_is_populated():
    m = matrix()
    assert len(m) >= 20, "expected a broad conversion matrix"
    for src, info in m.items():
        assert info["targets"], f"{src} has no targets"
        for t in info["targets"]:
            assert t["ext"] in FORMATS


def test_every_format_has_a_known_category():
    # The UI groups target chips by these categories.
    assert {f.category for f in FORMATS.values()} == set(CATEGORIES)


def test_all_registered_formats_are_known():
    for src in sources():
        assert src in FORMATS
        for t in targets_for(src):
            assert t in FORMATS


def test_no_identity_conversions():
    for src in sources():
        assert src not in targets_for(src)


def test_core_pairs_exist():
    # A few representative pairs from each family must be wired.
    for src, dst in [
        ("md", "pdf"),
        ("docx", "md"),
        ("pdf", "docx"),
        ("png", "jpg"),
        ("svg", "png"),
        ("png", "pdf"),
        ("csv", "json"),
        ("xlsx", "csv"),
        ("json", "yaml"),
        ("epub", "mobi"),
        ("mobi", "epub"),
        ("mp3", "wav"),
        ("wav", "flac"),
    ]:
        assert get_converter(src, dst) is not None, f"missing {src}->{dst}"


def test_health_and_formats_endpoints():
    # The web layer is an optional extra (requirements.web.txt); skip rather
    # than fail so the unit suite still runs on a box without Flask.
    pytest.importorskip("flask")
    from web_app import flask_app

    client = flask_app.test_client()
    assert client.get("/health").status_code == 200
    body = client.get("/api/formats").get_json()
    assert body.get("formats")
