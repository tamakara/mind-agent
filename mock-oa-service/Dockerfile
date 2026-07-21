FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
COPY mock-oa-service ./mock-oa-service
RUN pip install --no-cache-dir ./mock-oa-service
RUN mkdir -p /data/mock-oa && chown -R nobody:nogroup /data/mock-oa

USER nobody
EXPOSE 8001
CMD ["python", "-m", "mock_oa_service", "--serve"]
