from __future__ import annotations

from collections.abc import Generator

from pgvector.psycopg import register_vector
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from chat_api.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    # SQLAlchemy needs postgresql+psycopg:// for psycopg3
    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, pool_pre_ping=True, future=True)

    @event.listens_for(engine, "connect")
    def _register_vector(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        register_vector(dbapi_connection)

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def session_scope() -> Session:
    """Open a short-lived session (for background workflows)."""
    return SessionLocal()


def document_chunks_vector_dimension(provider: str | None = None) -> int | None:
    """Dimension of the active provider embedding table (None if missing)."""
    from chat_api.modules.embeddings import normalize_provider, provider_table_name

    table = provider_table_name(normalize_provider(provider))
    with engine.connect() as conn:
        value = conn.exec_driver_sql(
            "SELECT atttypmod FROM pg_attribute "
            f"WHERE attrelid = to_regclass('{table}') AND attname = 'embedding' AND NOT attisdropped"
        ).scalar()
    return int(value) if value is not None and value > 0 else None
