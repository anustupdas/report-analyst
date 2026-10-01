import pytest

from embedding_service.utils import initialize_configuration


@pytest.fixture(autouse=True)
def disable_startup_prefetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREFETCH_ON_START", "false")
    monkeypatch.setenv("WARMUP_ON_START", "false")
    initialize_configuration.cache_clear()
    yield
    initialize_configuration.cache_clear()
