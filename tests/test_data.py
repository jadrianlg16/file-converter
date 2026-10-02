"""Tests for the data (tabular) converters.

These are pandas-based and run whenever pandas + friends are importable; they
SKIP (not fail) on a bare box without those libraries. No external binaries are
needed.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converters import data, get_converter
from converters.engine import ConversionError

# Skip the whole module if pandas isn't available locally.
pd = pytest.importorskip("pandas")


def _sample_df():
    return pd.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["alice", "bob", "carol"],
            "score": [9.5, 8.0, 7.25],
        }
    )


def _assert_roundtrip(df, original):
    assert list(df.columns) == list(original.columns)
    assert df["id"].tolist() == original["id"].tolist()
    assert df["name"].tolist() == original["name"].tolist()
    assert df["score"].tolist() == original["score"].tolist()
    # numeric dtypes preserved (int stays int, float stays float)
    assert str(df["id"].dtype).startswith("int")
    assert str(df["score"].dtype).startswith("float")


def _write_csv(tmp_path):
    src = _sample_df()
    csv_path = os.path.join(tmp_path, "in.csv")
    src.to_csv(csv_path, index=False)
    return src, csv_path


@pytest.mark.parametrize("target", ["csv", "tsv", "json", "yaml", "yml"])
def test_csv_roundtrip_text_targets(tmp_path, target):
    """csv -> <target> -> csv preserves headers and numeric values."""
    src, csv_path = _write_csv(str(tmp_path))

    mid = os.path.join(str(tmp_path), f"mid.{target}")
    data.convert_tabular(csv_path, mid)
    assert os.path.exists(mid) and os.path.getsize(mid) > 0

    back = os.path.join(str(tmp_path), "back.csv")
    data.convert_tabular(mid, back)
    _assert_roundtrip(pd.read_csv(back), src)


def test_csv_to_xlsx_roundtrip(tmp_path):
    """csv -> xlsx -> csv (needs openpyxl)."""
    pytest.importorskip("openpyxl")
    src, csv_path = _write_csv(str(tmp_path))

    xlsx = os.path.join(str(tmp_path), "mid.xlsx")
    data.convert_tabular(csv_path, xlsx)
    assert os.path.exists(xlsx) and os.path.getsize(xlsx) > 0

    back = os.path.join(str(tmp_path), "back.csv")
    data.convert_tabular(xlsx, back)
    _assert_roundtrip(pd.read_csv(back), src)


def test_json_records_shape(tmp_path):
    """A list-of-records JSON loads as a normal table."""
    import json

    p = os.path.join(str(tmp_path), "records.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], fh)

    out = os.path.join(str(tmp_path), "out.csv")
    data.convert_tabular(p, out)
    df = pd.read_csv(out)
    assert list(df.columns) == ["a", "b"]
    assert df["a"].tolist() == [1, 2]
    assert df["b"].tolist() == ["x", "y"]


def test_json_columns_shape(tmp_path):
    """A dict-of-columns JSON is recognised as columns, not a single record."""
    import json

    p = os.path.join(str(tmp_path), "cols.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"a": [1, 2, 3], "b": [4, 5, 6]}, fh)

    out = os.path.join(str(tmp_path), "out.csv")
    data.convert_tabular(p, out)
    df = pd.read_csv(out)
    assert list(df.columns) == ["a", "b"]
    assert df["a"].tolist() == [1, 2, 3]
    assert df["b"].tolist() == [4, 5, 6]


def test_json_non_tabular_fallback(tmp_path):
    """Non-tabular JSON (list of scalars) falls back to a single 'value' column
    instead of crashing."""
    import json

    p = os.path.join(str(tmp_path), "scalars.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump([10, 20, 30], fh)

    out = os.path.join(str(tmp_path), "out.csv")
    data.convert_tabular(p, out)
    df = pd.read_csv(out)
    assert list(df.columns) == ["value"]
    assert df["value"].tolist() == [10, 20, 30]


def test_yaml_roundtrip(tmp_path):
    """csv -> yaml -> csv via the yaml branch (needs PyYAML)."""
    pytest.importorskip("yaml")
    src, csv_path = _write_csv(str(tmp_path))

    y = os.path.join(str(tmp_path), "mid.yaml")
    data.convert_tabular(csv_path, y)
    # YAML output should be a list of mappings, not a JSON blob string.
    import yaml

    with open(y, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    assert isinstance(loaded, list) and isinstance(loaded[0], dict)

    back = os.path.join(str(tmp_path), "back.csv")
    data.convert_tabular(y, back)
    _assert_roundtrip(pd.read_csv(back), src)


def test_export_html(tmp_path):
    """csv -> html renders a <table>."""
    _, csv_path = _write_csv(str(tmp_path))
    out = os.path.join(str(tmp_path), "out.html")
    data.export_table(csv_path, out)
    with open(out, encoding="utf-8") as fh:
        html = fh.read()
    assert "<table" in html
    assert "alice" in html
    assert "score" in html


def test_export_md(tmp_path):
    """csv -> md renders a pipe table (needs tabulate)."""
    pytest.importorskip("tabulate")
    _, csv_path = _write_csv(str(tmp_path))
    out = os.path.join(str(tmp_path), "out.md")
    data.export_table(csv_path, out)
    with open(out, encoding="utf-8") as fh:
        md = fh.read()
    assert "|" in md
    assert "name" in md
    assert "carol" in md


def test_invalid_json_raises_conversion_error(tmp_path):
    p = os.path.join(str(tmp_path), "bad.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    out = os.path.join(str(tmp_path), "out.csv")
    with pytest.raises(ConversionError):
        data.convert_tabular(p, out)


def test_registry_wiring():
    """The expected data pairs are registered and xls is source-only."""
    assert get_converter("csv", "json") is not None
    assert get_converter("xlsx", "csv") is not None
    assert get_converter("json", "yaml") is not None
    assert get_converter("xls", "csv") is not None  # xls source
    assert get_converter("csv", "html") is not None
    assert get_converter("json", "md") is not None
    # xls is source-only: nothing converts *to* xls.
    for src in ["csv", "json", "xlsx", "yaml"]:
        assert get_converter(src, "xls") is None


def test_export_html_is_a_complete_utf8_document(tmp_path):
    """A bare <table> fragment has no charset; browsers may show mojibake."""
    p = os.path.join(str(tmp_path), "in.csv")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("name\nJosé\n")
    out = os.path.join(str(tmp_path), "out.html")
    data.export_table(p, out)
    html = Path(out).read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert '<meta charset="utf-8">' in html
    assert "José" in html


@pytest.mark.parametrize("ext", ["json", "yaml"])
def test_utf8_bom_input_is_accepted(tmp_path, ext):
    # Notepad and Windows PowerShell 5.1 write UTF-8 with a BOM.
    body = '[{"a": 1, "b": "x"}]' if ext == "json" else "- a: 1\n  b: x\n"
    p = os.path.join(str(tmp_path), f"in.{ext}")
    with open(p, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + body.encode("utf-8"))
    out = os.path.join(str(tmp_path), "out.csv")
    data.convert_tabular(p, out)
    df = pd.read_csv(out)
    assert list(df.columns) == ["a", "b"]
    assert df["b"].tolist() == ["x"]


@pytest.mark.parametrize("ext,sep", [("csv", ","), ("tsv", "\t")])
def test_windows_1252_delimited_input_is_decoded(tmp_path, ext, sep):
    # What Excel on Windows writes for "CSV" — not UTF-8.
    import json

    p = os.path.join(str(tmp_path), f"in.{ext}")
    with open(p, "wb") as fh:
        fh.write(f"Año{sep}Señor\n2024{sep}Peña €\n".encode("cp1252"))
    out = os.path.join(str(tmp_path), "out.json")
    data.convert_tabular(p, out)
    assert json.loads(Path(out).read_text(encoding="utf-8")) == [{"Año": 2024, "Señor": "Peña €"}]


def test_utf8_bom_csv_header_is_clean(tmp_path):
    import json

    p = os.path.join(str(tmp_path), "in.csv")
    with open(p, "wb") as fh:
        fh.write(b"\xef\xbb\xbfid,name\n1,Ana\n")
    out = os.path.join(str(tmp_path), "out.json")
    data.convert_tabular(p, out)
    assert json.loads(Path(out).read_text(encoding="utf-8")) == [{"id": 1, "name": "Ana"}]


# --- Security: YAML alias bomb (FC-1) --------------------------------------


def test_yaml_aliases_are_refused(tmp_path):
    """A YAML anchor expanded many times can balloon a tiny file into
    gigabytes; the loader must refuse aliases outright."""
    pytest.importorskip("yaml")
    lines = ["l0: &l0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 7):
        refs = ", ".join([f"*l{i - 1}"] * 10)
        lines.append(f"l{i}: &l{i} [{refs}]")
    lines.append("top: *l6")
    src = os.path.join(str(tmp_path), "bomb.yaml")
    Path(src).write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = os.path.join(str(tmp_path), "out.json")
    with pytest.raises(ConversionError, match="alias"):
        data.convert_tabular(src, out)
    # nothing partial should have been written past the cap
    assert not os.path.exists(out) or os.path.getsize(out) < 1024 * 1024


def test_plain_yaml_without_aliases_still_works(tmp_path):
    pytest.importorskip("yaml")
    src = os.path.join(str(tmp_path), "ok.yaml")
    Path(src).write_text("- {id: 1, name: Ana}\n- {id: 2, name: Beto}\n", encoding="utf-8")
    out = os.path.join(str(tmp_path), "out.json")
    data.convert_tabular(src, out)
    import json

    assert json.loads(Path(out).read_text(encoding="utf-8")) == [
        {"id": 1, "name": "Ana"},
        {"id": 2, "name": "Beto"},
    ]


def test_output_size_cap_rejects_huge_result(tmp_path, monkeypatch):
    """The output-size cap refuses an oversized conversion before it lands."""
    monkeypatch.setattr(data, "_MAX_OUTPUT_BYTES", 1000)
    big = pd.DataFrame({"col": ["x" * 50] * 100})
    src = os.path.join(str(tmp_path), "big.csv")
    big.to_csv(src, index=False)
    out = os.path.join(str(tmp_path), "out.json")
    with pytest.raises(ConversionError, match="larger than"):
        data.convert_tabular(src, out)
