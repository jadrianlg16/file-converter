# Multi-format File Converter.
# Engines: Pandoc (+WeasyPrint) for text docs, LibreOffice for Office formats,
# Calibre for ebooks, ffmpeg for audio, Pillow/CairoSVG/PyMuPDF for images/PDF.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        libreoffice-writer libreoffice-calc libreoffice-impress \
        calibre \
        ffmpeg \
        # WeasyPrint runtime (HTML/CSS -> PDF) \
        libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 libffi8 \
        shared-mime-info \
        # fonts so PDFs/images render real glyphs; Carlito/Caladea are \
        # metric-compatible with Calibri/Cambria so LibreOffice paginates \
        # office docs like Word does \
        fonts-dejavu fonts-liberation fonts-noto-core \
        fonts-crosextra-carlito fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.web.txt pins direct deps and pulls in constraints.txt (-c).
COPY requirements.web.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.web.txt

COPY web_app.py ./
COPY demo_guard.py ./
COPY converters ./converters
COPY templates ./templates
COPY static ./static

# Build stamp: regenerated whenever any code layer above changes, exposed at
# /health and in the UI footer so a stale container is immediately visible.
RUN date -u +"%Y-%m-%d %H:%M UTC" > /app/BUILD_STAMP

RUN mkdir -p /app/data
ENV DATA_DIR=/app/data
# Calibre's PDF output renders via Qt WebEngine (Chromium), which aborts when
# run as root unless the sandbox is disabled; there is no GPU in the container.
ENV QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu"

EXPOSE 5007
CMD ["gunicorn", "--bind", "0.0.0.0:5007", "--workers", "2", "--timeout", "300", "web_app:flask_app"]
