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
        # fonts so PDFs/images render real glyphs \
        fonts-dejavu fonts-liberation fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.web.txt .
RUN pip install --no-cache-dir -r requirements.web.txt

COPY web_app.py ./
COPY converters ./converters
COPY templates ./templates
COPY static ./static

RUN mkdir -p /app/data
ENV DATA_DIR=/app/data

EXPOSE 5007
CMD ["gunicorn", "--bind", "0.0.0.0:5007", "--workers", "2", "--timeout", "300", "web_app:flask_app"]
