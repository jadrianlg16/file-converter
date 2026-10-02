# File Converter design notes

How the code is organised and the conventions that keep it consistent. Setup
and usage are in [README.md](README.md).

## Layout

```text
web_app.py              Flask: /api/formats, /convert, /health, / (UI)
demo_guard.py           opt-in DEMO_MODE limits (stdlib only)
converters/
  registry.py           FORMATS + register()/get_converter()/matrix()
  engine.py             subprocess helpers: pandoc/soffice/ffmpeg/ebook-convert
  documents.py          text docs + PDF (pandoc, LibreOffice, PyMuPDF, pdf2docx)
  pdf_docx_fixup/       layout repairs for pdf2docx output (one module per repair)
  images.py             raster/svg/img<->pdf (Pillow, CairoSVG, PyMuPDF)
  audio.py              audio (ffmpeg)
  data.py               csv/tsv/json/xlsx/xls/yaml (pandas)
  ebooks.py             epub/mobi/azw3/fb2 (Calibre)
templates/index.html    the page
static/app.js, style.css
tests/                  pytest; engine tests skip if the binary is missing
legacy/app.py           older tkinter md->docx tool, independent of the rest
```

## Handlers

Every conversion is a function `fn(in_path: str, out_path: str) -> None` that
reads `in_path`, writes the converted file to `out_path`, and raises on
failure:

- `engine.ConversionError` carries a message the user can act on; the API
  returns it with status 422 and the UI shows it verbatim.
- Any other exception is a bug. It is logged with its traceback and the
  client gets a generic 500.

The web layer deletes both paths afterwards, including a partial `out_path`
left by a failed handler, so handlers don't clean up after themselves on
error. Heavy libraries are imported inside the handlers, so the package still
imports on a machine without them.

## Registration

Each format module registers its pairs at import time:

```python
from .registry import register, register_many

register("md", "pdf", md_to_pdf)  # one pair
register_many(SRCS, DSTS, handler)  # cartesian product, skips equal pairs
```

Every extension must exist in `registry.FORMATS`. The last registration wins,
so two modules never register the same `(src, dst)` pair; the boundaries below
say which module owns it.

## Module boundaries

- `documents.py`: text hub (md/markdown/rst/txt/html/htm/docx/odt/rtf/tex/latex/epub) any↔any; all→pdf, including epub→pdf; pdf → txt/md/html/docx (best effort).
- `images.py`: raster↔raster (png/jpg/jpeg/webp/gif/bmp/tiff/tif); svg→raster; raster→pdf; pdf → every raster format (first page).
- `audio.py`: mp3/wav/ogg/flac/m4a/aac any↔any.
- `data.py`: csv/tsv/json/xlsx/xls/yaml/yml hub (xls is read-only, Excel is written as xlsx); csv/tsv/xlsx/xls/json → html/md tables.
- `ebooks.py`: epub/mobi/azw3/fb2 any↔any; mobi/azw3/fb2 → pdf.

## External tools

All subprocess calls go through `converters/engine.py`: `have(bin)`,
`require(bin)`, `run(cmd, timeout=)`, `pandoc(...)`,
`soffice_convert(in, out_dir, target_ext, convert_filter=)`,
`ffmpeg(in, out, extra=)` and `ebook_convert(in, out, extra=)`. `run()` takes
an argument list, never a shell string, and enforces a timeout.

`pandoc()` always passes `--sandbox`, so a document can't pull server files
into its output, and lets WeasyPrint fetch only `data:` URIs. That needs a
pandoc whose docx/odt/epub writers work in the sandbox; the Docker image
installs 3.11.

## HTTP API

Consumed by `static/app.js`:

- `GET /api/formats` → `{"formats": {"<ext>": {"name", "category", "targets": [{"ext", "name", "category"}]}}, "maxUploadMb": N}`, plus `"demo"` when DEMO_MODE is on.
- `POST /convert`, multipart `file` + `target` → the file with status 200, or
  JSON `{"error": "..."}` with 400/403/413/422/429/500. The UI decides
  success by status alone, because `.json` outputs are `application/json` too.
- `GET /health` → `{"status": "ok", "formats": N, "build": "<stamp>"}`

## Conversion quality

- Round-trips aim to preserve content in both directions: headings, emphasis, lists,
  tables, links and code blocks for documents; mode, alpha and orientation for
  images; types and headers for data.
- Edge cases are handled explicitly: empty files, RGBA→JPEG (flattened),
  non-tabular JSON, multi-page PDFs, BOM and Windows-encoded text input.
- The PDF→DOCX repairs in `pdf_docx_fixup/` are best-effort. Each one runs
  through `documents._best_effort()`, which falls back to the unrepaired
  output and logs the failure. A silent fallback could hide a crash while page
  counts still pass, so the regression tests assert structure (headers, tab
  stops, shading, cell contents), not only page counts.

## Tests

- Tests live in `tests/test_<module>.py`. Tests that need an engine use
  `@requires("pandoc")` (from `conftest.py`) and skip when the binary is
  absent; fixtures are generated inside the tests.
- `conftest.py` points `DATA_DIR` at a throwaway temp dir.
- The engine-dependent tests only all run inside the Docker image.
- New system or pip dependencies go in the `Dockerfile` or
  `requirements.web.txt` with a comment saying what needs them.
