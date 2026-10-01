"""Dedicated encode workers: one forever for query, one forever for passage.

Requests never share a worker. A long passage batch cannot occupy the query
slot, so search stays responsive while ingest runs (at the cost of a second
in-memory model copy). Call ``warmup()`` at startup to load both copies before
traffic arrives.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from embedding_service.service import EmbeddingService
from embedding_service.utils import ServiceConfig

logger = logging.getLogger(__name__)

__all__ = ("DedicatedEncodePool",)

_STOP = object()


@dataclass(frozen=True)
class _EncodeJob:
    texts: list[str]
    model_name: str | None
    input_type: str
    future: Future


class DedicatedEncodePool:
    """Two worker threads with separate model instances and dedicated queues."""

    def __init__(
        self,
        config: ServiceConfig,
        *,
        query_service: EmbeddingService | None = None,
        passage_service: EmbeddingService | None = None,
    ) -> None:
        self.config = config
        self._query_service = query_service or EmbeddingService(config=config)
        self._passage_service = passage_service or EmbeddingService(config=config)
        self._query_queue: queue.Queue[Any] = queue.Queue()
        self._passage_queue: queue.Queue[Any] = queue.Queue()
        self._query_thread = threading.Thread(
            target=self._worker_loop,
            name="embed-query-worker",
            args=("query", self._query_queue, self._query_service),
            daemon=True,
        )
        self._passage_thread = threading.Thread(
            target=self._worker_loop,
            name="embed-passage-worker",
            args=("passage", self._passage_queue, self._passage_service),
            daemon=True,
        )
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._query_thread.start()
            self._passage_thread.start()
            self._started = True
            logger.info("Dedicated encode pool started (query worker + passage worker)")

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._started:
                return
            self._query_queue.put(_STOP)
            self._passage_queue.put(_STOP)
            self._query_thread.join(timeout=timeout)
            self._passage_thread.join(timeout=timeout)
            self._started = False
            logger.info("Dedicated encode pool stopped")

    def is_model_ready(self, model_name: str | None = None) -> bool:
        """True when at least one lane has loaded the model."""
        return self._query_service.is_model_ready(model_name) or self._passage_service.is_model_ready(model_name)

    def lane_ready(self, lane: str, model_name: str | None = None) -> bool:
        if lane == "query":
            return self._query_service.is_model_ready(model_name)
        if lane == "passage":
            return self._passage_service.is_model_ready(model_name)
        raise ValueError("lane must be 'query' or 'passage'")

    def both_lanes_ready(self, model_name: str | None = None) -> bool:
        return self.lane_ready("query", model_name) and self.lane_ready("passage", model_name)

    def warmup(self, *, model_name: str | None = None, timeout: float = 600.0) -> None:
        """Load both lane models into RAM via a tiny encode on each worker.

        Lanes are warmed **one after the other**. Loading two SentenceTransformer
        copies concurrently can fail with torch meta-tensor errors.
        """
        if not self._started:
            self.start()
        text = "warmup"
        logger.info("Warming encode lanes sequentially (query, then passage)")
        self.submit([text], model_name=model_name, input_type="query").result(timeout=timeout)
        logger.info("Query lane warm")
        self.submit([text], model_name=model_name, input_type="passage").result(timeout=timeout)
        logger.info(
            "Warmup complete query_ready=%s passage_ready=%s",
            self.lane_ready("query", model_name),
            self.lane_ready("passage", model_name),
        )

    async def warmup_async(self, *, model_name: str | None = None, timeout: float = 600.0) -> None:
        await asyncio.to_thread(self.warmup, model_name=model_name, timeout=timeout)

    def submit(
        self,
        texts: list[str],
        *,
        model_name: str | None = None,
        input_type: str = "passage",
    ) -> Future:
        if input_type not in {"passage", "query"}:
            raise ValueError("input_type must be 'passage' or 'query'")
        if not self._started:
            self.start()
        future: Future = Future()
        job = _EncodeJob(texts=texts, model_name=model_name, input_type=input_type, future=future)
        target = self._query_queue if input_type == "query" else self._passage_queue
        target.put(job)
        return future

    async def embed_texts(
        self,
        texts: list[str],
        *,
        model_name: str | None = None,
        input_type: str = "passage",
    ) -> tuple[list[dict], dict]:
        future = self.submit(texts, model_name=model_name, input_type=input_type)
        return await asyncio.wrap_future(future)

    @staticmethod
    def _worker_loop(lane: str, work_queue: queue.Queue, service: EmbeddingService) -> None:
        logger.info("Encode worker ready lane=%s", lane)
        while True:
            item = work_queue.get()
            try:
                if item is _STOP:
                    logger.info("Encode worker stopping lane=%s", lane)
                    return
                job: _EncodeJob = item
                if job.future.set_running_or_notify_cancel():
                    try:
                        result = service.embed_texts(
                            job.texts,
                            model_name=job.model_name,
                            input_type=job.input_type,
                        )
                        job.future.set_result(result)
                    except Exception as exc:
                        job.future.set_exception(exc)
            finally:
                work_queue.task_done()
