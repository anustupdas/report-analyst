FROM python:3.12-slim

RUN useradd -u 1001 -m -s /bin/bash app

WORKDIR /app
COPY apps/langgraph-server/pyproject.toml apps/langgraph-server/README.md ./
COPY apps/langgraph-server/src ./src
COPY apps/langgraph-server/configs ./configs

RUN pip install --no-cache-dir . \
 && chown -R app:app /app

ENV LANGGRAPH_CONFIG_PATH=/app/configs/staging/config.yml

USER app
EXPOSE 8080

CMD ["run-cli"]
