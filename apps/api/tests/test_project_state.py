from types import SimpleNamespace
from uuid import uuid4

from chat_api.modules.actions import document_to_resource


def test_document_to_resource_includes_artifact_paths():
    document_id = uuid4()
    artifact = SimpleNamespace(
        id=uuid4(),
        identifier="extracted-text",
        storage_path="user/project/doc/extracted-text.txt",
        mime_type="text/plain",
    )
    document = SimpleNamespace(
        id=document_id,
        project_id=uuid4(),
        user_id=uuid4(),
        title="Shell AR 2024",
        original_filename="shell.pdf",
        mime_type="application/pdf",
        status="completed",
        company_name="Shell plc",
        report_year=2024,
        doc_type="annual_report",
        summary="Integrated annual report.",
        key_datapoints=None,
        error_message=None,
        created_at=None,
        updated_at=None,
        artifacts=[artifact],
    )
    resource = document_to_resource(document)
    assert resource["extractedTextPath"] == "user/project/doc/extracted-text.txt"
    assert resource["originalFileUrl"] == f"/api/v1/projects/{document.project_id}/documents/{document.id}/original"
    assert resource["artifacts"][0]["identifier"] == "extracted-text"
    assert resource["companyName"] == "Shell plc"
    assert resource["keyDatapoints"] is None


def test_document_to_resource_includes_key_datapoints():
    document = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        user_id=uuid4(),
        title="Shell AR 2024",
        original_filename="shell.pdf",
        mime_type="application/pdf",
        status="completed",
        company_name="Shell plc",
        report_year=2024,
        doc_type="annual_report",
        summary="Integrated annual report.",
        key_datapoints={"fte": {"value": 90000}, "sustainability_goals": []},
        error_message=None,
        created_at=None,
        updated_at=None,
        artifacts=[],
    )
    resource = document_to_resource(document)
    assert resource["keyDatapoints"]["fte"]["value"] == 90000
