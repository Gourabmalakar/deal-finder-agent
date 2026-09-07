# Playwright's official image ships Chromium plus every OS dependency it
# needs — avoids a long, fragile apt-get list for headless Chromium's font/
# codec/sandbox requirements. Pin the tag to the installed playwright pip
# version (see backend/requirements.txt) so the browser build matches the
# library.
#
# Build context must be the REPO ROOT (not webapp/), since data/ lives one
# level above webapp/. On Render: set "Dockerfile Path" to
# webapp/Dockerfile and "Docker Build Context Directory" to . (repo root).
# Locally: docker build -f webapp/Dockerfile .   (run from the repo root)
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

COPY webapp/backend/requirements.txt ./webapp/backend/requirements.txt
RUN pip install --no-cache-dir -r webapp/backend/requirements.txt

COPY webapp/backend ./webapp/backend
COPY webapp/frontend ./webapp/frontend
COPY data ./data

WORKDIR /app/webapp/backend
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
