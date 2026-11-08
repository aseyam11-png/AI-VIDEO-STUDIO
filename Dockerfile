FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

ARG INSTALL_BLENDER=0

WORKDIR /app
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg ca-certificates && \
    if [ "$INSTALL_BLENDER" = "1" ]; then \
      apt-get install -y --no-install-recommends blender; \
    fi && \
    rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md alembic.ini ./
COPY migrations ./migrations
COPY backend ./backend
ARG INSTALL_EXTRAS=media
RUN pip install --no-cache-dir ".[${INSTALL_EXTRAS}]"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
