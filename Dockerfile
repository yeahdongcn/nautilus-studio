ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.11-slim

FROM ${NODE_IMAGE} AS web-build

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web ./
RUN npm run build

FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STUDIO_DATA_DIR=/var/lib/nautilus \
    STUDIO_WEB_ROOT=/app/web

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin nautilus \
    && mkdir -p /var/lib/nautilus \
    && chown -R nautilus:nautilus /var/lib/nautilus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=web-build /web/dist ./web
RUN pip install --no-cache-dir .

VOLUME ["/var/lib/nautilus"]
EXPOSE 7860
USER nautilus

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=3)"

CMD ["nautilus-studio", "--host", "0.0.0.0", "--port", "7860"]
