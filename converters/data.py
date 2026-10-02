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

import logging
import os
from typing import TYPE_CHECKING, Any

from .engine import ConversionError
from .registry import register_many

if TYPE_CHECKING:  # pandas is imported lazily, inside the handlers
    import pandas as pd

log = logging.getLogger(__name__)

DATA_IN = ["csv", "tsv", "json", "xlsx", "xls", "yaml", "yml"]
DATA_OUT = ["csv", "tsv", "json", "xlsx", "yaml", "yml"]  # note: no .xls writer by default

_TABULAR_SRC = ["csv", "tsv", "xlsx", "xls", "json"]

# Cap on the serialized size of a data conversion's output. YAML anchors (and,
# in theory, a pathological JSON) can expand a tiny input into gigabytes: a
# 400-byte YAML with nested anchors produced 500+ MiB. The output is built in
# memory before it is written, so refuse anything past this.
_MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def _no_alias_loader() -> type:
    """A YAML SafeLoader that refuses aliases, so an anchor can't be expanded
    many times over (a 400-byte file otherwise balloons into gigabytes)."""
    import yaml

    class NoAliasSafeLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):  # yaml composer hook
            if self.check_event(yaml.events.AliasEvent):
                raise ConversionError(
                    "YAML aliases/anchors are not supported (they can blow up the output)."
                )
            return super().compose_node(parent, index)

    return NoAliasSafeLoader


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
            log.warning("Invalid JSON input", exc_info=True)
            raise ConversionError("The file is not valid JSON.") from e
        return _frame_from_obj(obj)
    if src in ("yaml", "yml"):
        import yaml

        try:
            with open(in_path, encoding="utf-8-sig") as fh:
                # NoAliasSafeLoader subclasses SafeLoader and refuses aliases.
                obj = yaml.load(fh, Loader=_no_alias_loader())
        except yaml.YAMLError as e:
            log.warning("Invalid YAML input", exc_info=True)
            raise ConversionError("The file is not valid YAML.") from e
        return _frame_from_obj(obj)

    raise ConversionError(f"Unsupported data source format: {src!r}")


def _too_big() -> ConversionError:
    return ConversionError(
        f"The converted output is larger than the {_MAX_OUTPUT_BYTES // (1024 * 1024)} MB "
        "limit for data conversions."
    )


def _write_text(out_path: str, text: str) -> None:
    """Write ``text`` as UTF-8, refusing output past the size cap before it
    touches disk."""
    if len(text.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise _too_big()
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _check_output_size(out_path: str) -> None:
    """Refuse a produced file (xlsx) that is past the size cap."""
    if os.path.getsize(out_path) > _MAX_OUTPUT_BYTES:
        raise _too_big()


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
        log.warning("Failed to read %s input", _ext(in_path), exc_info=True)
        raise ConversionError(
            f"Could not read the .{_ext(in_path)} file — it may be malformed."
        ) from e

    dst = _ext(out_path)
    try:
        if dst == "csv":
            _write_text(out_path, df.to_csv(index=False))
        elif dst == "tsv":
            _write_text(out_path, df.to_csv(sep="\t", index=False))
        elif dst == "xlsx":
            df.to_excel(out_path, index=False, engine="openpyxl")
            _check_output_size(out_path)
        elif dst == "json":
            import json

            _write_text(
                out_path,
                json.dumps(_df_to_native(df), ensure_ascii=False, indent=2),
            )
        elif dst in ("yaml", "yml"):
            import yaml

            _write_text(
                out_path,
                yaml.safe_dump(
                    _df_to_native(df),
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                ),
            )
        else:
            raise ConversionError(f"Unsupported data target format: {dst!r}")
    except ConversionError:
        raise
    except ImportError as e:
        raise ConversionError(f"Missing data dependency: {e}") from e
    except Exception as e:  # the writers raise many types; report any as a failure
        log.warning("Failed to write %s output", dst, exc_info=True)
        raise ConversionError(f"Could not write the .{dst} output.") from e


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
        log.warning("Failed to read %s input", _ext(in_path), exc_info=True)
        raise ConversionError(
            f"Could not read the .{_ext(in_path)} file — it may be malformed."
        ) from e

    dst = _ext(out_path)
    try:
        if dst in ("html", "htm"):
            # A complete document with a declared charset: a bare <table>
            # fragment can show non-ASCII text as mojibake when opened.
            table = df.to_html(index=False, border=1, na_rep="")
            _write_text(
                out_path,
                '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n'
                "<title>Table</title>\n</head>\n<body>\n" + table + "\n</body>\n</html>\n",
            )
        elif dst in ("md", "markdown"):
            # to_markdown needs `tabulate`.
            _write_text(out_path, df.to_markdown(index=False) + "\n")
        else:
            raise ConversionError(f"Unsupported table export target: {dst!r}")
    except ConversionError:
        raise
    except ImportError as e:
        raise ConversionError(
            f"Missing data dependency (markdown export needs 'tabulate'): {e}"
        ) from e
    except Exception as e:  # the writers raise many types; report any as a failure
        log.warning("Failed to write %s output", dst, exc_info=True)
        raise ConversionError(f"Could not write the .{dst} output.") from e


# Tabular hub.
register_many(DATA_IN, DATA_OUT, convert_tabular)
# Table exports to document formats (one-way niceties).
register_many(_TABULAR_SRC, ["html", "md"], export_table)
