#!/bin/sh
# Align model name and dimension with EMBEDDING_PROVIDER before the API starts.
set -eu

provider=$(printf '%s' "${EMBEDDING_PROVIDER:-openai}" | tr '[:upper:]' '[:lower:]')
case "$provider" in
  huggingface|hf|local|gte)
    export EMBEDDING_PROVIDER=huggingface
    export EMBEDDING_MODEL_NAME=gte-multilingual-base
    export EMBEDDING_DIMENSION=768
    ;;
  openai|oai)
    export EMBEDDING_PROVIDER=openai
    export EMBEDDING_MODEL_NAME=text-embedding-3-small
    export EMBEDDING_DIMENSION=1536
    ;;
  *)
    echo "EMBEDDING_PROVIDER must be openai or huggingface (got: ${EMBEDDING_PROVIDER})" >&2
    exit 1
    ;;
esac

exec "$@"
