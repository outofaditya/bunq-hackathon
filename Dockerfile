# syntax=docker/dockerfile:1.7
# ============================================================================
# Mission Mode — production image
#   stage 1: build the React/Vite dashboard
#   stage 2: Playwright Python base (Chromium + headless deps already inside)
#            install Python deps, copy backend, copy built frontend
# ============================================================================

# ----------------------------------------------------------------------------
# Stage 1: build the React frontend
# ----------------------------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /app/dashboard-react

# Cache npm install — only re-run when manifest changes
COPY dashboard-react/package.json dashboard-react/package-lock.json* ./
RUN npm ci --no-audit --no-fund

# Build
COPY dashboard-react/ ./
RUN npm run build


# ----------------------------------------------------------------------------
# Stage 2: Python runtime with Chromium pre-installed
# ----------------------------------------------------------------------------
# Note: this image already has Playwright + Chromium + system fonts/codecs.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    PUBLIC_BASE_URL=""

WORKDIR /app

# Python deps — install first so they cache when source changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Backend source
COPY orchestrator/ ./orchestrator/
COPY mock_sites/ ./mock_sites/
COPY assets/ ./assets/
COPY bunq_client.py 01_authentication.py 02_create_monetary_account.py \
     03_list_monetary_accounts.py 03_make_payment.py 04_request_money.py \
     05_create_bunqme_link.py 06_list_transactions.py 07_setup_callbacks.py \
     ./

# Built frontend
COPY --from=frontend /app/dashboard-react/dist ./dashboard-react/dist

# Runtime tts cache directory (writable by non-root)
RUN mkdir -p assets/tts_cache && chmod -R a+rwX assets/tts_cache /app

# Drop privileges — image ships with `pwuser` (uid 1000)
USER pwuser

EXPOSE 8000

# Healthcheck — App Runner / ECS use this for instance liveness
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
                 urllib.request.urlopen(f'http://127.0.0.1:{__import__(\"os\").environ.get(\"PORT\",\"8000\")}/health', timeout=2); \
                 sys.exit(0)" || exit 1

# Direct uvicorn invocation — picks up $PORT (App Runner / Cloud Run convention)
CMD ["sh", "-c", "exec uvicorn orchestrator.server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
