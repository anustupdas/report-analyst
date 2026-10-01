FROM python:3.12-slim

ARG RUNENV=staging
ARG USERNAME=text-extraction-service

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -u 1001 -m -s /bin/bash ${USERNAME}

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs

RUN pip install --no-cache-dir . \
 && mkdir -p /etc/${USERNAME}/data \
 && chown -R ${USERNAME}:${USERNAME} /app /etc/${USERNAME}

COPY configs/${RUNENV}/config.yml /etc/${USERNAME}/config.yml
ENV REMOTE_CONFIG_URL=/etc/${USERNAME}/config.yml
ENV LOCAL_DATA_DIR=/etc/${USERNAME}/data

USER ${USERNAME}
EXPOSE 5000

CMD ["run-cli"]
