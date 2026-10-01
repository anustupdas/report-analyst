#!/usr/bin/env python3
"""Compare HF vs OpenAI embedding throughput (ingest batch size = 32)."""

from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
EMB_ENV = ROOT / "apps" / "embedding-service" / ".env"
BASE = os.environ.get("EMBEDDING_URL", "http://127.0.0.1:5100").rstrip("/")

# Typical annual-report chunk size (~2000 chars).
SAMPLE = (
    "Risk, funding & capital. Net profit attributable to shareholders was EUR 2.4 billion. "
    "The Common Equity Tier 1 ratio stood at 13.8 percent. Operating expenses decreased "
    "as digitalisation programmes matured. Sustainability goals include net-zero lending "
    "by 2050 and a reduction of financed emissions in the energy portfolio. "
) * 8  # ~2k chars


def load_openai_key() -> None:
    vals = dotenv_values(EMB_ENV)
    key = (vals.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if key:
        os.environ["OPENAI_API_KEY"] = key


def make_batch(n: int) -> list[str]:
    return [f"{SAMPLE} chunk={i}" for i in range(n)]


def time_http(path: str, payload: dict, rounds: int) -> list[float]:
    times: list[float] = []
    with httpx.Client(timeout=180.0, trust_env=False) as client:
        # warmup
        r = client.post(f"{BASE}{path}", json=payload)
        r.raise_for_status()
        for _ in range(rounds):
            t0 = time.perf_counter()
            r = client.post(f"{BASE}{path}", json=payload)
            r.raise_for_status()
            times.append(time.perf_counter() - t0)
            body = r.json()
            assert len(body["embeddings"]) == len(payload["texts"])
    return times


def main() -> None:
    load_openai_key()
    batch_size = int(os.environ.get("BATCH_SIZE", "32"))
    rounds = int(os.environ.get("ROUNDS", "3"))
    texts = make_batch(batch_size)

    print(f"base={BASE} batch_size={batch_size} rounds={rounds} chars≈{len(texts[0])}")

    # Health
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        docs = client.get(f"{BASE}/docs")
        print(f"embedding-service /docs -> {docs.status_code}")

    hf_payload = {
        "texts": texts,
        "model_name": "gte-multilingual-base",
        "input_type": "passage",
    }
    oai_payload = {
        "texts": texts,
        "model_name": "text-embedding-3-small",
    }

    print("\n=== Hugging Face (local gte) POST /api/v1/embed_texts ===")
    hf = time_http("/api/v1/embed_texts", hf_payload, rounds)
    print(f"times_s={[round(t, 3) for t in hf]}")
    print(f"mean_s={statistics.mean(hf):.3f}  per_chunk_ms={1000 * statistics.mean(hf) / batch_size:.1f}")

    print("\n=== OpenAI text-embedding-3-small POST /api/v1/embeddings/openai ===")
    oai = time_http("/api/v1/embeddings/openai", oai_payload, rounds)
    print(f"times_s={[round(t, 3) for t in oai]}")
    print(f"mean_s={statistics.mean(oai):.3f}  per_chunk_ms={1000 * statistics.mean(oai) / batch_size:.1f}")

    ratio = statistics.mean(hf) / statistics.mean(oai)
    print("\n=== Summary (lower is faster) ===")
    print(f"HF mean batch:     {statistics.mean(hf):.3f}s")
    print(f"OpenAI mean batch: {statistics.mean(oai):.3f}s")
    print(f"HF/OpenAI ratio:   {ratio:.2f}x  (>1 means OpenAI faster for this batch)")
    # Rough full-report estimate at 32/batch
    for label, chunks in [("Apple~few hundred", 200), ("ABN AMRO large", 2500)]:
        batches = (chunks + batch_size - 1) // batch_size
        print(
            f"est. embed-only {label} (~{chunks} chunks): "
            f"HF {batches * statistics.mean(hf):.0f}s | "
            f"OpenAI {batches * statistics.mean(oai):.0f}s"
        )


if __name__ == "__main__":
    main()
