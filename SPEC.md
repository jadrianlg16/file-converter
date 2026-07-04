# File Converter — build contract (read this first)

A registry-driven multi-format file converter. A web UI lets a user drop a file,
see every format it can become, pick one, and download the result. Conversions
should be **bidirectional wherever it makes sense** and **high quality**.

## Architecture

```
file-converter/
  web_app.py            # Flask: /api/formats, /convert, /health, / (UI)   [ORCHESTRATOR owns]
  converters/
    registry.py         # FORMATS + register()/get_converter()/matrix()    [ORCHESTRATOR owns]
    engine.py           # subprocess helpers: pandoc/soffice/ffmpeg/...     [ORCHESTRATOR owns]
    documents.py        # text docs + PDF                 [AGENT: documents]
    images.py           # raster/svg/img<->pdf            [AGENT: media]
    audio.py            # audio                            [AGENT: media]
    data.py             # csv/tsv/json/xlsx/xls/yaml       [AGENT: data+ebooks]
    ebooks.py           # epub/mobi/azw3/fb2               [AGENT: data+ebooks]
  templates/index.html  # UI                               [AGENT: ui]
  static/app.js,style.css  # UI                            [AGENT: ui]
  tests/                # pytest; engine tests skip if binary missing
  Dockerfile, requirements.web.txt                         [ORCHESTRATOR owns]
```

## The contract (do not change these signatures)

**Handler:** every conversion is a function `fn(in_path: str, out_path: str) -> None`
that reads `in_path`, writes the converted file to `out_path`, and raises on
failure (prefer `engine.ConversionError` with a clear message).

**Registration** (at import time, in your module):
```python
from .registry import register, register_many
register("md", "pdf", my_md_to_pdf)          # one pair
register_many(SRCS, DSTS, my_handler)        # cartesian product, skips equal pairs
```
The stub modules already register the full intended matrix pointing at a `_todo`
placeholder that raises `NotImplementedError`. **Replace `_todo` with real
handlers** — keep the same registered pairs (or refine them and say so in your
report). Do not invent new top-level formats; they must exist in `FORMATS`.

**Engine helpers** (`converters/engine.py`) — reuse these, don't re-spawn shells:
`have(bin)`, `require(bin)`, `run(cmd, timeout=)`, `pandoc(in,out,...)`,
`soffice_convert(in,out_dir,target_ext,filter=)`, `ffmpeg(in,out,extra=)`,
`ebook_convert(in,out,extra=)`, and `ConversionError`.

**API (already implemented, for the UI agent):**
- `GET /api/formats` → `{"formats": { "<srcext>": {"name","category","targets":[{"ext","name","category"}]}}}`
- `POST /convert` multipart: `file` + `target` (target extension) → file download, or JSON `{"error": "..."}` with status 400/422/500/501.
- `GET /health` → `{"status":"ok","formats":N}`

## Module boundaries (avoid double-registering the same pair)

- `documents.py`: text hub (md/markdown/rst/txt/html/htm/docx/odt/rtf/tex/latex/epub) any↔any; all→pdf; **pdf → txt/md/html/docx** (best effort). Owns **epub→pdf**.
- `images.py`: raster↔raster (png/jpg/jpeg/webp/gif/bmp/tiff/tif); svg→raster; raster→pdf; **pdf → png/jpg/jpeg/tiff** (rasterize pages).
- `audio.py`: mp3/wav/ogg/flac/m4a/aac any↔any.
- `data.py`: csv/tsv/json/xlsx/xls/yaml/yml hub (xls source-only; write xlsx); plus csv/tsv/xlsx/xls/json → html/md tables.
- `ebooks.py`: epub/mobi/azw3/fb2 any↔any; **mobi/azw3/fb2 → pdf** (not epub→pdf).

If two modules would register the same `(src,dst)`, the boundaries above decide
the owner. Flag any overlap you find in your report.

## Quality bar — "vice versa and great"

- Round-trips must actually work both directions and preserve content: headings,
  bold/italic, lists, tables, links, code blocks for docs; mode/alpha/orientation
  for images; numeric types/headers for data; tags/metadata for audio where cheap.
- Handle edge cases: empty files, RGBA→JPEG (flatten), non-tabular JSON, multi-page
  PDFs, large files (stream/temp, don't load everything in memory when avoidable).
- Clear errors via `ConversionError` (the UI shows the message verbatim).

## Testing

- Put tests in `tests/test_<module>.py`. Use `from conftest import requires` and
  decorate engine-dependent tests with `@requires("pandoc")` etc. so they SKIP
  (not fail) when the binary is absent locally. Generate tiny fixtures in-test.
- Run: `python -m pytest tests/ -q`. The registry/wiring tests
  (`tests/test_registry.py`) must stay green.

## Do NOT touch
`registry.py`, `engine.py`, `web_app.py`, `Dockerfile`, `requirements.web.txt`.
If you need a new pip/apt dependency, **state it in your final report** and the
orchestrator will add it. Work only in your assigned files.
```
