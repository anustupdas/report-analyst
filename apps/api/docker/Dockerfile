FROM python:3.12-slim

RUN useradd -u 1001 -m -s /bin/bash app

WORKDIR /app
COPY apps/api/pyproject.toml apps/api/README.md ./
COPY apps/api/src ./src
COPY apps/api/configs ./configs
COPY apps/web /web
COPY apps/api/docker/entrypoint.sh /entrypoint.sh

RUN pip install --no-cache-dir . \
 && chmod +x /entrypoint.sh \
 && chown -R app:app /app /web

ENV API_CONFIG_PATH=/app/configs/staging/config.yml
ENV WEB_STATIC_DIR=/web
ENV DATA_ROOT=/data

USER app
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["run-cli"]
