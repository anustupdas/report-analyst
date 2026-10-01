from __future__ import annotations

from langgraph_server.core.project_context import document_catalog, format_project_context, usable_document_ids
from langgraph_server.prompt.loader import load_prompt


def test_load_supervisor_fallback_prompt():
    prompt = load_prompt("supervisor_agent", "supervisor", "main")
    assert prompt.source == "fallback"
    assert prompt.model_config.model_provider == "openai"
    assert "max_report_searches" in prompt.variables
    compiled = prompt.compile(max_report_searches=5)
    text = compiled[0]["content"]
    assert "report-search-tool" in text
    assert "At most 5" in text
    assert "Prefer **1–2** strong searches" in text


def test_max_report_searches_setting_defaults():
    from langgraph_server.config import Settings

    assert Settings.model_fields["supervisor_max_report_searches_per_turn"].default == 5


def test_langfuse_prompt_labels_follow_environment():
    from langgraph_server.config import Settings

    assert Settings(environment="development", _env_file=None).langfuse_prompt_labels == ["dev", "production"]
    assert Settings(environment="production", _env_file=None).langfuse_prompt_labels == ["production"]


def test_format_project_context_includes_inventory_and_pending():
    snapshot = {
        "pendingSourcesCount": 2,
        "sources": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "title": "Shell AR 2024",
                "originalFilename": "shell.pdf",
                "companyName": "Shell plc",
                "reportYear": 2024,
                "processStatus": "completed",
                "summary": "Integrated annual report.",
                "extractedTextPath": "u/p/d/extracted-text.txt",
            }
        ],
    }
    text = format_project_context(snapshot)
    assert "PROJECT CONTEXT STATUS" in text
    assert "Pending sources: 2" in text
    assert "document_id: 11111111-1111-1111-1111-111111111111" in text
    assert "extracted_text_path: u/p/d/extracted-text.txt" in text
    assert usable_document_ids(snapshot) == ["11111111-1111-1111-1111-111111111111"]
    assert document_catalog(snapshot)["11111111-1111-1111-1111-111111111111"]["originalFilename"] == "shell.pdf"


def test_format_project_context_empty():
    text = format_project_context({"pendingSourcesCount": 0, "sources": []})
    assert "No sources available." in text
    assert usable_document_ids({"sources": []}) == []
