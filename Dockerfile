FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARRNEXUS_SELF_UPDATE=1

WORKDIR /opt/arrnexus-seed

# ffprobe powers Language Guard/TV recovery. 7zip powers review-first RAR recovery.
# python3-venv is used by the native updater to isolate future dependencies under /data.
RUN apt-get update \
    && (apt-get install -y --no-install-recommends ffmpeg p7zip-full || apt-get install -y --no-install-recommends ffmpeg 7zip) \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Keep a read-only seed inside the image. The bootstrap copies this release to
# /data/runtime on first boot; future application releases are staged there by
# ArrNexus itself, leaving the database and operator data in /data untouched.
COPY app ./app
COPY validate.py validate_v10.py validate_v7.py validate_v8.py validate_v9.py validate_v91.py validate_v92.py validate_v93.py validate_v94.py validate_v101.py validate_v102.py validate_v103.py validate_v104.py ./
COPY generate_help_docs.py migrate_legacy_env.py README.md CHANGELOG.md VALIDATION.md docker-compose.yml .env.example ./
COPY docs ./docs
COPY examples ./examples
COPY bootstrap.py /opt/arrnexus-bootstrap.py

RUN mkdir -p /data/runtime /data/backups

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=35s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()" || exit 1

CMD ["python", "/opt/arrnexus-bootstrap.py"]
