#!/bin/sh
# Load the local model only when embeddings are Hugging Face.
set -eu

provider=$(printf '%s' "${EMBEDDING_PROVIDER:-openai}" | tr '[:upper:]' '[:lower:]')
case "$provider" in
  huggingface|hf|local|gte)
    export PREFETCH_ON_START=true
    export WARMUP_ON_START=true
    ;;
  *)
    export PREFETCH_ON_START=false
    export WARMUP_ON_START=false
    ;;
esac

exec "$@"
