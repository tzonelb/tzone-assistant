FROM python:3.13-slim

WORKDIR /app

# System deps needed by weasyprint-adjacent PDF/Arabic text libs
# (reportlab/arabic-reshaper/python-bidi) and cryptography's build.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# database/ and uploads/ are mounted as volumes in docker-compose so
# data survives image rebuilds/redeploys — created here too so the
# container still works if run standalone without those mounts.
RUN mkdir -p database uploads

# Run as a non-root user: a container escape or RCE in a dependency
# (or in the app itself) shouldn't hand the attacker root inside the
# container. Own the app + volume-mount dirs so gunicorn/sqlite can
# still write to them under the new user.
RUN groupadd --system tzone && useradd --system --gid tzone --home /app tzone \
    && chown -R tzone:tzone /app
USER tzone

EXPOSE 8000

# 4 workers is a reasonable default for a small/medium VPS; raise
# WEB_CONCURRENCY via the environment if the box has more CPU headroom.
CMD ["sh", "-c", "gunicorn main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-4} -b 0.0.0.0:8000 --timeout 120"]
