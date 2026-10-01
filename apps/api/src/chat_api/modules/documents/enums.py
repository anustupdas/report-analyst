from __future__ import annotations

from enum import StrEnum


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    INGESTING = "ingesting"
    READY = "ready"
    COMPLETED = "completed"
    FAILED = "failed"

    @classmethod
    def claimable(cls) -> tuple["DocumentStatus", ...]:
        return (cls.PENDING, cls.FAILED)

    @classmethod
    def usable(cls) -> tuple["DocumentStatus", ...]:
        """Searchable. `ready` still has batches landing; `completed` is the full index."""
        return (cls.READY, cls.COMPLETED)

    @classmethod
    def in_flight(cls) -> tuple["DocumentStatus", ...]:
        return (cls.PENDING, cls.PROCESSING, cls.EXTRACTED, cls.INGESTING, cls.READY)


class ArtifactIdentifier(StrEnum):
    ORIGINAL_PDF = "original-pdf"
    ORIGINAL = "original"
    EXTRACTED_TEXT = "extracted-text"
    EXTRACTED_LAYOUT = "extracted-layout"
    SUMMARY = "summary"

    @classmethod
    def originals(cls) -> tuple["ArtifactIdentifier", ...]:
        return (cls.ORIGINAL_PDF, cls.ORIGINAL)


class AgentMemoryKind(StrEnum):
    PREFERENCE = "preference"
    FACT = "fact"
    NOTE = "note"
