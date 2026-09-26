# File Converter — contributor contract (read this first)

A registry-driven multi-format file converter. A web UI lets a user drop a file,
see every format it can become, pick one, and download the result. Conversions
should be **bidirectional wherever it makes sense** and **high quality**.
User-facing docs live in [README.md](README.md); this file is the contract for
changing the code.

## Layout

```
file-converter/
  web_app.py            # Flask: /api/formats, /convert, /health, / (UI)
  demo_guard.py         # opt-in DEMO_MODE limits (stdlib only)
  converters/
    registry.py         # FORMATS + register()/get_converter()/matrix()
    engine.py           # subprocess helpers: pandoc/soffice/ffmpeg/ebook-convert
    documents.py        # text docs + PDF (pandoc, LibreOffice, PyMuPDF, pdf2docx)
    pdf_docx_fixup.py   # layout repairs for pdf2docx output
    images.py           # raster/svg/img<->pdf (Pillow, CairoSVG, PyMuPDF)
    audio.py            # audio (ffmpeg)
    data.py             # csv/tsv/json/xlsx/xls/yaml (pandas)
    ebooks.py           # epub/mobi/azw3/fb2 (Calibre)
  templates/index.html  # UI
  static/app.js,style.css
  tests/                # pytest; engine tests skip if the binary is missing
  Dockerfile, requirements.web.txt
  app.py                # legacy tkinter md->docx tool, independent of the rest
```

## The contract (do not change these signatures)

**Handler:** every conversion is a function `fn(in_path: str, out_path: str) -> None`
that reads `in_path`, writes the converted file to `out_path`, and raises on
failure — `engine.ConversionError` with a message the user can act on (the UI
shows it verbatim). Any other exception becomes a generic 500 and is logged.
The web layer deletes both paths afterwards, including a partial `out_path`
left by a failed handler, so handlers needn't clean up after themselves on
error. Heavy libraries are imported lazily *inside* handlers so the package
imports on a box without them.

**Registration** (at import time, in the format module):
```python
from .registry import register, register_many
register("md", "pdf", my_md_to_pdf)          # one pair
register_many(SRCS, DSTS, my_handler)        # cartesian product, skips equal pairs
```
Every extension must exist in `registry.FORMATS`. Last registration wins, so
two modules must never register the same `(src, dst)` — the boundaries below
decide the owner.

**Engine helpers** (`converters/engine.py`) — reuse these, don't re-spawn shells:
`have(bin)`, `require(bin)`, `run(cmd, timeout=)`, `pandoc(in,out,...)`,
`soffice_convert(in,out_dir,target_ext,convert_filter=)`, `ffmpeg(in,out,extra=)`,
`ebook_convert(in,out,extra=)`, and `ConversionError`.

**API** (consumed by `static/app.js`):
- `GET /api/formats` → `{"formats": { "<srcext>": {"name","category","targets":[{"ext","name","category"}]}}, "maxUploadMb": N}` (+ `"demo"` when DEMO_MODE is on)
- `POST /convert` multipart: `file` + `target` (target extension) → the file with
  status 200, or JSON `{"error": "..."}` with 400/403/413/422/429/500/501. The UI
  must decide success by status alone: `.json` outputs are `application/json` too.
- `GET /health` → `{"status":"ok","formats":N,"build":"<stamp>"}`

## Module boundaries

- `documents.py`: text hub (md/markdown/rst/txt/html/htm/docx/odt/rtf/tex/latex/epub) any↔any; all→pdf; **pdf → txt/md/html/docx** (best effort). Owns **epub→pdf**.
- `images.py`: raster↔raster (png/jpg/jpeg/webp/gif/bmp/tiff/tif); svg→raster; raster→pdf; **pdf → every raster format** (first page).
- `audio.py`: mp3/wav/ogg/flac/m4a/aac any↔any.
- `data.py`: csv/tsv/json/xlsx/xls/yaml/yml hub (xls source-only; Excel is written as xlsx); plus csv/tsv/xlsx/xls/json → html/md tables.
- `ebooks.py`: epub/mobi/azw3/fb2 any↔any; **mobi/azw3/fb2 → pdf** (not epub→pdf).

## Quality bar — "vice versa and great"

- Round-trips must work both directions and preserve content: headings,
  bold/italic, lists, tables, links, code blocks for docs; mode/alpha/orientation
  for images; numeric types/headers for data; tags/metadata for audio where cheap.
- Handle edge cases: empty files, RGBA→JPEG (flatten), non-tabular JSON, multi-page
  PDFs, BOM / Windows-encoded text input, large files (stream/temp, don't load
  everything in memory when avoidable).
- PDF→DOCX repairs in `pdf_docx_fixup.py` are best-effort and each is guarded
  with a fallback to plain pdf2docx output. Because a silent fallback can hide a
  crash while page counts still pass, regression tests should assert structure
  (headers, tab stops, shading, cell contents), not only page counts.

## Testing

- Put tests in `tests/test_<module>.py`. Use `from conftest import requires` and
  decorate engine-dependent tests with `@requires("pandoc")` etc. so they SKIP
  (not fail) when the binary is absent locally. Generate tiny fixtures in-test.
- Run: `python -m pytest tests/ -q`. `conftest.py` points `DATA_DIR` at a
  throwaway temp dir. The engine-dependent tests only run fully inside the
  Docker image (pandoc, LibreOffice, Calibre, ffmpeg).
- New system or pip dependencies go in the `Dockerfile` / `requirements.web.txt`
  with a comment saying what needs them.
