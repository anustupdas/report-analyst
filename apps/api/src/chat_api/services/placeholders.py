"""Outbound clients that are not implemented yet — keep call sites stable."""

from __future__ import annotations

import logging
from typing import Literal, Protocol

from chat_api.config import Settings
from chat_api.tracing import structured_log

logger = logging.getLogger(__name__)

DocType = Literal["annual_report", "other"]


class DocumentClassifierClient(Protocol):
    def classify(self, text: str, *, request_id: str | None = None) -> DocType:
        pass


class PlaceholderDocumentClassifier:
    """Stand-in for the annual-report classifier (JEV model); ingest stores no doc_type yet."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def classify(self, text: str, *, request_id: str | None = None) -> DocType:
        structured_log(logger, "classifier.placeholder_skip", request_id=request_id, chars=len(text))
        raise NotImplementedError("Document classifier is not wired yet")
