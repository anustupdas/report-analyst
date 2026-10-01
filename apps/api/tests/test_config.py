from __future__ import annotations

from pathlib import Path

import pytest

from chat_api.config import API_ROOT, REPO_ROOT, YAML_KEYS, Settings


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in (
        "DATABASE_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "AGENT_SECRET",
        "INTERNAL_WORKFLOW_SECRET",
        "OPENAI_API_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_HOST",
        "LANGFUSE_BASE_URL",
        "API_PORT",
        "CHUNK_MAX_CHARS",
        "DATA_ROOT",
        "WEB_STATIC_DIR",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_MODEL_NAME",
        "EMBEDDING_DIMENSION",
        "TEXT_EXTRACTION_USE_OCR",
    ):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "config.yml"
    config.write_text(
        "api.port: 9100\n"
        "database.host: db.internal\n"
        "database.port: 6543\n"
        "database.name: reports\n"
        "storage.allowedExtensions: [pdf, txt]\n"
        "chunking.maxChars: 1200\n"
    )
    monkeypatch.setenv("API_CONFIG_PATH", str(config))
    return tmp_path


def _settings(tmp_path: Path, env_text: str = "") -> Settings:
    env_file = tmp_path / ".env"
    env_file.write_text(env_text)
    return Settings(_env_file=str(env_file))


def test_yaml_supplies_non_secret_settings(isolated: Path) -> None:
    settings = _settings(isolated)
    assert settings.api_port == 9100
    assert settings.chunk_max_chars == 1200
    assert settings.allowed_extension_set == frozenset({"pdf", "txt"})


def test_dotenv_only_supplies_secrets(isolated: Path) -> None:
    settings = _settings(
        isolated,
        "POSTGRES_USER=svc\nPOSTGRES_PASSWORD=p@ss/word\nAGENT_SECRET=s3cret\nAPI_PORT=1234\nCHUNK_MAX_CHARS=5\n",
    )
    assert settings.agent_secret == "s3cret"
    assert settings.api_port == 9100
    assert settings.chunk_max_chars == 1200
    assert settings.database_url == "postgresql://svc:p%40ss%2Fword@db.internal:6543/reports"


def test_process_env_still_overrides_yaml(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "9999")
    assert _settings(isolated).api_port == 9999


def test_explicit_database_url_wins(isolated: Path) -> None:
    settings = _settings(isolated, "DATABASE_URL=postgresql://a:b@h:1/d\n")
    assert settings.database_url == "postgresql://a:b@h:1/d"


def test_shipped_configs_cover_every_key() -> None:
    import yaml

    for env in ("staging", "production"):
        raw = yaml.safe_load((API_ROOT / "configs" / env / "config.yml").read_text())
        assert set(raw) == set(YAML_KEYS), env


def test_langfuse_prompt_labels_follow_environment(isolated: Path) -> None:
    assert _settings(isolated).langfuse_prompt_labels == ["dev", "production"]
    assert Settings(environment="production", _env_file=None).langfuse_prompt_labels == ["production"]


def test_default_paths_resolve_to_repo(isolated: Path) -> None:
    settings = _settings(isolated)
    assert settings.data_root == REPO_ROOT / "data"
    assert settings.web_static_dir == REPO_ROOT / "apps" / "web"
