from chat_api.services.chunking import TextChunk
from chat_api.workflows.ingest import EMBED_BATCH_SIZE, stream_embeddings


def _chunk(index: int) -> TextChunk:
    return TextChunk(
        chunk_index=index,
        content=f"chunk {index}",
        section=None,
        page_start=index + 1,
        page_end=index + 1,
        token_count=2,
    )


def test_stream_embeddings_stores_each_batch_before_the_next_and_marks_the_first() -> None:
    windows = [_chunk(i) for i in range(70)]
    calls: list[int] = []
    stored: list[tuple[int, bool]] = []

    def embed(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        # The next batch must not be requested before this one was stored.
        assert len(stored) == len(calls) - 1
        return [[float(len(text))] for text in texts]

    def store(batch, vectors, first: bool) -> None:
        assert len(vectors) == len(batch)
        stored.append((len(batch), first))

    total = stream_embeddings(windows, batch_size=EMBED_BATCH_SIZE, embed=embed, store=store)

    assert total == 70
    assert calls == [32, 32, 6]
    assert stored == [(32, True), (32, False), (6, False)]


def test_stream_embeddings_rejects_a_short_vector_list() -> None:
    try:
        stream_embeddings([_chunk(0), _chunk(1)], batch_size=32, embed=lambda texts: [[]], store=lambda *args: None)
    except RuntimeError as exc:
        assert "1 vectors" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
