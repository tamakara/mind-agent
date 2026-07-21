FROM node:22-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --fetch-retries=5 --fetch-retry-mintimeout=20000 --fetch-retry-maxtimeout=120000
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKHUB_STATIC_DIR=/app/frontend-dist

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY --from=frontend-build /frontend/dist /app/frontend-dist
RUN mkdir -p /data/workhub && chown -R nobody:nogroup /data/workhub

USER nobody
EXPOSE 8000
CMD ["python", "-m", "workhub"]
