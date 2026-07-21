FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN mkdir -p /data/workhub && chown -R nobody:nogroup /data/workhub

USER nobody
EXPOSE 8000
CMD ["python", "-m", "workhub"]
