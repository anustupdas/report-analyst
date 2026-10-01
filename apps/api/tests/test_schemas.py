from uuid import uuid4

from chat_api.http.v1.schemas import CreateUserRequest, ProjectResponse, RegenerateTokenRequest


def test_create_user_accepts_camel_display_name():
    body = CreateUserRequest.model_validate({"email": "you@example.com", "displayName": "Poka"})
    assert body.display_name == "Poka"


def test_regenerate_token_request():
    body = RegenerateTokenRequest.model_validate({"email": "you@example.com"})
    assert str(body.email) == "you@example.com"


def test_project_response_serializes_thread_id():
    project_id = uuid4()
    user_id = uuid4()
    thread_id = uuid4()
    payload = ProjectResponse(
        id=project_id,
        name="Shell 2024",
        user_id=user_id,
        thread_id=thread_id,
    ).model_dump(by_alias=True)
    assert payload["threadId"] == thread_id
    assert payload["userId"] == user_id
    assert payload["id"] == project_id
    assert payload["threadId"] != payload["id"]
