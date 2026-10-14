FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY sync.py ./
COPY write_server.py ./
RUN pip install .

# Einziger Schreiber von garmin_cache (A.2/D4); Token-Volume unter /data/token (G.3).
CMD ["python", "sync.py"]
