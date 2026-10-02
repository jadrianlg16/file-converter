# Multi-format File Converter.
# Engines: Pandoc (+WeasyPrint) for text docs, LibreOffice for Office formats,
# Calibre for ebooks, ffmpeg for audio, Pillow/CairoSVG/PyMuPDF for images/PDF.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
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

# Pandoc comes from its official release, not Debian's 3.1.11 package: that
# build can't write docx/odt/epub under --sandbox, and --sandbox is what stops
# an uploaded document from pulling server files into its output. Pinned by
# version and checksum for both architectures.
RUN set -eu; \
    version=3.11; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) sum=37edb3bbcf722f921a009941bf5874e2e0c09263226c9b4a2d980788cb062ab6 ;; \
        arm64) sum=56ed5566ec41d22ec9ee0704e6ac0b98ba102e92384efd5306173a22d314c79a ;; \
        *) echo "no pandoc release for $arch" >&2; exit 1 ;; \
    esac; \
    python -c 'import sys, urllib.request; urllib.request.urlretrieve(*sys.argv[1:])' \
        "https://github.com/jgm/pandoc/releases/download/$version/pandoc-$version-linux-$arch.tar.gz" \
        /tmp/pandoc.tar.gz; \
    echo "$sum  /tmp/pandoc.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/pandoc.tar.gz -C /usr/local --strip-components=1 "pandoc-$version/bin/pandoc"; \
    rm /tmp/pandoc.tar.gz; \
    pandoc --version | head -n 1

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
