# File Converter

A universal file converter with a drag-and-drop web UI. Upload a file, see every
format it can become, pick one, and download the result. Conversions are
bidirectional wherever it makes sense, across **5 families / ~38 formats**.

## Supported conversions

| Family | Formats | Engine |
|---|---|---|
| **Documents** | md, markdown, rst, txt, html, htm, docx, odt, rtf, tex/latex, epub — any ↔ any; all → pdf | Pandoc (+ WeasyPrint for PDF), LibreOffice for Office→PDF fidelity |
| **PDF (in)** | pdf → txt, md, html, docx | PyMuPDF + pdf2docx (best-effort) |
| **Images** | png, jpg, jpeg, webp, gif, bmp, tiff, tif — any ↔ any; svg → raster; raster ↔ pdf | Pillow, CairoSVG, PyMuPDF |
| **Data** | csv, tsv, json, xlsx, xls, yaml/yml — any ↔ any; + table → html/md | pandas, openpyxl, PyYAML |
| **Ebooks** | epub, mobi, azw3, fb2 — any ↔ any; mobi/azw3/fb2 → pdf | Calibre (`ebook-convert`) |
| **Audio** | mp3, wav, ogg, flac, m4a, aac — any ↔ any | ffmpeg |

Notes: `xls` is read-only (Excel is written as `xlsx`). `svg` is a source only.
PDF → editable formats is **best-effort** (no OCR — scanned/image-only and
password-protected PDFs are rejected with a clear error). PDF → image
rasterizes the first page.

PDF → docx goes well beyond raw pdf2docx (see `converters/pdf_docx_fixup.py`):
repeated letterheads/footers become **real Word headers/footers** (bucketed
left/center/right, varying page numbers become PAGE/NUMPAGES fields, the
separator rule becomes a border); label/value and column rows become tab-stop
paragraphs at the real x-positions instead of strangled layout tables; ruled
tables get their true column widths from the drawn borders; merged list lines
are split back into paragraphs; wrongly-justified ragged text is left-aligned
again; and the result is render-verified with LibreOffice so source pages
don't spill onto extra docx pages. Regression harness: `tests/pdf_fixtures.py`
+ `tests/test_pdf_layout.py`.

## Architecture

The set of conversions is **registry-driven** — nothing is hard-coded in the web
layer. Each format family is a module that registers its `(source → target)`
handlers; the UI asks the backend what's possible.

```
web_app.py              Flask API: GET /api/formats, POST /convert, /health, / (UI)
converters/
  registry.py           FORMATS + register()/get_converter()/matrix()
  engine.py             subprocess helpers (pandoc, soffice, ffmpeg, ebook-convert)
  documents.py          documents + PDF
  images.py  audio.py   images + audio
  data.py    ebooks.py  data + ebooks
templates/ static/      drag-drop UI (vanilla JS, no build step, offline)
tests/                  pytest (engine-dependent tests skip when a binary is absent)
```

A handler is just `fn(in_path, out_path) -> None` that raises
`engine.ConversionError` on failure. See [SPEC.md](SPEC.md) for the full contract.

## Run

### Docker (recommended — bundles every engine)
```bash
docker build -t sidetools/file-converter:latest .
docker run --rm -p 5007:5007 sidetools/file-converter:latest
# open http://localhost:5007
```
This is also how it's wired into the Project Dashboard (`file-converter`, port 5007).

### Local (Python)
You need the system engines on PATH for the families you want: `pandoc`,
`libreoffice` (`soffice`), `calibre` (`ebook-convert`), `ffmpeg`. Then:
```bash
pip install -r requirements.web.txt
python web_app.py            # http://localhost:5007
```

## API

- `GET /api/formats` → `{"formats": {"<ext>": {"name","category","targets":[...]}}}`
- `POST /convert` — multipart `file` + `target` (extension) → file download, or JSON `{"error":"..."}`
- `GET /health` → `{"status":"ok","formats":N}`

## Develop / test

```bash
pip install -r requirements.dev.txt
python -m pytest tests/ -q
```
Tests that need an external binary **skip** (not fail) when it isn't installed,
so the suite is green on a bare box and fully exercised inside the Docker image.

> The original tkinter desktop tool (`app.py`, Markdown→DOCX only) is kept for
> backward compatibility and is independent of the web service.
