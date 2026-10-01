import os

# Unit tests must not create a live Langfuse client (YAML has tracing on).
os.environ["LANGFUSE_TRACING"] = "false"
