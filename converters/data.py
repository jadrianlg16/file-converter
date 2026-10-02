"""Data / tabular conversions (pandas + openpyxl + xlrd + PyYAML).

Scope:
  * Tabular hub: csv, tsv, json, xlsx, xls, yaml, yml -> each other.
      - xls is a SOURCE only; Excel output is always written as xlsx.
      - xlsx/xls: only the first worksheet is read.
      - json: records (list-of-objects) and {column: [values]} shapes; other
        JSON falls back to a single "value" column (or one record for a dict).
      - yaml/yml: same shapes as json.
      - csv/tsv: read as UTF-8 (BOM tolerated); files saved by Excel on
        Windows (cp1252 / Latin-1) are decoded via a fallback.
  * Convenience exports: csv, tsv, xlsx, xls, json -> html and md (render a
    table). One-way niceties, not a full doc engine.

All heavy libraries are imported lazily *inside* the handlers so this module
(and therefore the whole ``converters`` package) imports cleanly even when
pandas / openpyxl / xlrd / PyYAML are not installed locally.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from .engine import ConversionError
from .registry import register_many

if TYPE_CHECKING:  # pandas is imported lazily, inside the handlers
    import pandas as pd

DATA_IN = ["csv", "tsv", "json", "xlsx", "xls", "yaml", "yml"]
DATA_OUT = ["csv", "tsv", "json", "xlsx", "yaml", "yml"]  # note: no .xls writer by default

_TABULAR_SRC = ["csv", "tsv", "xlsx", "xls", "json"]


# --- helpers ----------------------------------------------------------------


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".")


def _records_from_obj(obj: Any) -> tuple[Any, str | None]:
    """Normalise an arbitrary JSON/YAML object into a list of record dicts.

    Handles:
      * list of objects  -> as-is (each item coerced to a dict if scalar)
      * dict of columns   -> {"a": [1,2], "b": [3,4]} (equal-length -> columns)
      * dict of scalars   -> single record
      * anything else      -> single-column fallback ``{"value": [...]}``
    """
    if isinstance(obj, list):
        if all(isinstance(item, dict) for item in obj):
            return obj, None  # records orientation; let pandas infer columns
        # list of scalars / mixed -> single column
        return [{"value": item} for item in obj], None

    if isinstance(obj, dict):
        values = list(obj.values())
        # dict-of-columns: every value is a list and lengths agree
        if values and all(isinstance(v, list) for v in values):
            lengths = {len(v) for v in values}
            if len(lengths) == 1:
                return obj, "columns"
        # dict-of-scalars (or ragged) -> one record
        return [obj], None

    # scalar / None -> single cell
    return [{"value": obj}], None


# Tried in order for csv/tsv. utf-8-sig also strips a BOM; cp1252 covers CSVs
# saved by Excel on Windows; latin-1 maps every byte, so it never fails.
_TEXT_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


def _read_delimited(in_path: str, sep: str) -> pd.DataFrame:
    """Read csv/tsv, trying each of _TEXT_ENCODINGS in turn."""
    import pandas as pd

    for encoding in _TEXT_ENCODINGS[:-1]:
        try:
            return pd.read_csv(in_path, sep=sep, encoding=encoding)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(in_path, sep=sep, encoding=_TEXT_ENCODINGS[-1])


def _frame_from_obj(obj: Any) -> pd.DataFrame:
    """Parsed JSON/YAML object -> DataFrame (see ``_records_from_obj``)."""
    import pandas as pd

    records, orient = _records_from_obj(obj)
    if orient == "columns":
        return pd.DataFrame(records)
    return pd.DataFrame.from_records(records)


def _read_df(in_path: str) -> pd.DataFrame:
    """Read ``in_path`` into a pandas DataFrame based on its extension."""
    import pandas as pd

    src = _ext(in_path)

    if src == "csv":
        return _read_delimited(in_path, ",")
    if src == "tsv":
        return _read_delimited(in_path, "\t")
    if src == "xlsx":
        return pd.read_excel(in_path, engine="openpyxl")
    if src == "xls":
        # Legacy Excel — xlrd is the only reader that handles the BIFF format.
        return pd.read_excel(in_path, engine="xlrd")
    if src == "json":
        import json

        # utf-8-sig: Notepad and PowerShell 5.1 save JSON/YAML with a BOM,
        # which json.load() otherwise rejects.
        try:
            with open(in_path, encoding="utf-8-sig") as fh:
                obj = json.load(fh)
        except ValueError as e:
            raise ConversionError(f"Invalid JSON input: {e}") from e
        return _frame_from_obj(obj)
    if src in ("yaml", "yml"):
        import yaml

        try:
            with open(in_path, encoding="utf-8-sig") as fh:
                obj = yaml.safe_load(fh)
        except yaml.YAMLError as e:
            raise ConversionError(f"Invalid YAML input: {e}") from e
        return _frame_from_obj(obj)

    raise ConversionError(f"Unsupported data source format: {src!r}")


def _df_to_native(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame -> list-of-records using JSON-friendly native types.

    Round-trips through pandas' JSON serializer so NaN/NaT, numpy ints/floats
    and timestamps become plain Python values that json/yaml can emit.
    """
    import json

    return json.loads(df.to_json(orient="records", date_format="iso"))


# --- hub handler ------------------------------------------------------------


def convert_tabular(in_path: str, out_path: str) -> None:
    """csv/tsv/json/xlsx/xls/yaml/yml -> csv/tsv/json/xlsx/yaml/yml."""
    try:
        df = _read_df(in_path)
    except ConversionError:
        raise
    except ImportError as e:
        raise ConversionError(f"Missing data dependency: {e}") from e
    except Exception as e:  # the parsers raise many types; report any as a bad file
        raise ConversionError(f"Failed to read {_ext(in_path)} input: {e}") from e

    dst = _ext(out_path)
    try:
        if dst == "csv":
            df.to_csv(out_path, index=False)
        elif dst == "tsv":
            df.to_csv(out_path, sep="\t", index=False)
        elif dst == "xlsx":
            df.to_excel(out_path, index=False, engine="openpyxl")
        elif dst == "json":
            import json

            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(_df_to_native(df), fh, ensure_ascii=False, indent=2)
        elif dst in ("yaml", "yml"):
            import yaml

            with open(out_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(
                    _df_to_native(df),
                    fh,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
        else:
            raise ConversionError(f"Unsupported data target format: {dst!r}")
    except ConversionError:
        raise
    except ImportError as e:
        raise ConversionError(f"Missing data dependency: {e}") from e
    except Exception as e:  # the writers raise many types; report any as a failure
        raise ConversionError(f"Failed to write {dst} output: {e}") from e


# --- table exports to document formats (one-way niceties) -------------------


def export_table(in_path: str, out_path: str) -> None:
    """csv/tsv/xlsx/xls/json -> html / md (render a table)."""
    try:
        df = _read_df(in_path)
    except ConversionError:
        raise
    except ImportError as e:
        raise ConversionError(f"Missing data dependency: {e}") from e
    except Exception as e:  # the parsers raise many types; report any as a bad file
        raise ConversionError(f"Failed to read {_ext(in_path)} input: {e}") from e

    dst = _ext(out_path)
    try:
        if dst in ("html", "htm"):
            # A complete document with a declared charset: a bare <table>
            # fragment can show non-ASCII text as mojibake when opened.
            table = df.to_html(index=False, border=1, na_rep="")
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(
                    '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n'
                    "<title>Table</title>\n</head>\n<body>\n"
                )
                fh.write(table)
                fh.write("\n</body>\n</html>\n")
        elif dst in ("md", "markdown"):
            # to_markdown needs `tabulate`.
            md = df.to_markdown(index=False)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(md)
                fh.write("\n")
        else:
            raise ConversionError(f"Unsupported table export target: {dst!r}")
    except ConversionError:
        raise
    except ImportError as e:
        raise ConversionError(
            f"Missing data dependency (markdown export needs 'tabulate'): {e}"
        ) from e
    except Exception as e:  # the writers raise many types; report any as a failure
        raise ConversionError(f"Failed to write {dst} output: {e}") from e


# Tabular hub.
register_many(DATA_IN, DATA_OUT, convert_tabular)
# Table exports to document formats (one-way niceties).
register_many(_TABULAR_SRC, ["html", "md"], export_table)
