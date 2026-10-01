FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -u 1001 -m -s /bin/bash app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
COPY docker/entrypoint.sh /entrypoint.sh

# CPU torch satisfies the Hugging Face extra. The default path is OpenAI and
# does not load this model (see PREFETCH_ON_START / WARMUP_ON_START).
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir . \
 && chmod +x /entrypoint.sh \
 && chown -R app:app /app

ENV REMOTE_CONFIG_URL=/app/configs/staging/config.yml
ENV PREFETCH_ON_START=false
ENV WARMUP_ON_START=false

USER app
EXPOSE 5100

ENTRYPOINT ["/entrypoint.sh"]
CMD ["run-cli"]
