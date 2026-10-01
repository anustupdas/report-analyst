from typing import Any, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph import MessagesState


class SupervisorGraphState(MessagesState):
    project_inventory_context: NotRequired[str]
    model_context_messages: NotRequired[list[AnyMessage]]
    model_context_transcript_count: NotRequired[int]
    key_datapoints: NotRequired[dict[str, Any]]
    datapoints_pages: NotRequired[list[int]]
