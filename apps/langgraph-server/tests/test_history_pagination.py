from __future__ import annotations

from langgraph_server.core.project_context import HistoryMessage
from langgraph_server.http.supervisor import _paginate_newest_first


def test_paginate_empty():
    result = _paginate_newest_first([], page=1, size=20)
    assert result == {"messages": [], "total": 0, "page": 1, "size": 20, "pages": 0}


def test_paginate_newest_first_keeps_in_page_chronology():
    messages = [HistoryMessage(role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(5)]
    page1 = _paginate_newest_first(messages, page=1, size=2)
    page2 = _paginate_newest_first(messages, page=2, size=2)
    page3 = _paginate_newest_first(messages, page=3, size=2)

    assert page1["total"] == 5
    assert page1["pages"] == 3
    assert [item["content"] for item in page1["messages"]] == ["m3", "m4"]
    assert [item["content"] for item in page2["messages"]] == ["m1", "m2"]
    assert [item["content"] for item in page3["messages"]] == ["m0"]


def test_paginate_clamps_page_past_end():
    messages = [HistoryMessage(role="user", content="only")]
    result = _paginate_newest_first(messages, page=9, size=20)
    assert result["page"] == 1
    assert result["pages"] == 1
    assert result["messages"][0]["content"] == "only"
