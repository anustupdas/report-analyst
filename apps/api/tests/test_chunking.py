from __future__ import annotations

from chat_api.services.chunking import PageText, chunk_pages, chunk_text, split_sentences

_WORDS = "revenue margin capital liquidity climate workforce clients mortgages lending deposits".split()


def _sentences(count: int, prefix: str = "Topic") -> str:
    return " ".join(
        f"{prefix} sentence {i} explains {_WORDS[(i + len(prefix)) % 10]} and {_WORDS[(i * 3 + ord(prefix[-1])) % 10]} "
        f"reaching EUR 1.{i} billion."
        for i in range(count)
    )


def _wrap(text: str, width: int = 60) -> str:
    """Simulate PyMuPDF hard line breaks."""
    lines, current = [], ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    lines.append(current)
    return "\n".join(lines)


def _report_page(number: int, body: str, chapter: str, section: str) -> PageText:
    footer = f"≡\n{chapter}\n{section}\nACME Group\nAnnual Report 2025\n{number}"
    return PageText(page_number=number, text=f"{_wrap(body)}\n{footer}")


def test_empty_input() -> None:
    assert chunk_text("   ") == []
    assert chunk_pages([]) == []


def test_chunks_respect_max_and_start_on_sentences() -> None:
    text = "\n\n".join(_sentences(12, prefix=f"Para{p}") for p in range(10))
    chunks = chunk_text(text, target_chars=1000, max_chars=1300, overlap_sentences=2)

    assert len(chunks) > 3
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(len(c.content) <= 1300 for c in chunks)
    assert all(c.content[0].isupper() for c in chunks)
    assert all(c.content.endswith(".") for c in chunks)
    assert all(c.token_count == len(c.content.split()) for c in chunks)


def test_overlap_repeats_whole_sentences() -> None:
    chunks = chunk_text(_sentences(60), target_chars=800, max_chars=1000, overlap_sentences=2)
    first, second = chunks[0], chunks[1]
    last_two = split_sentences(first.content)[-2:]
    assert split_sentences(second.content)[:2] == last_two


def test_overlong_sentence_splits_on_words() -> None:
    sentence = " ".join(["word"] * 400) + "."
    chunks = chunk_text(sentence, target_chars=300, max_chars=400)
    assert all(len(c.content) <= 400 for c in chunks)
    assert all(not c.content.startswith("ord") for c in chunks)


def test_running_footer_becomes_section_and_is_stripped() -> None:
    names = ["Theta", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
    pages = [
        _report_page(n, _sentences(8, prefix=names[n - 1]), "Risk, funding & capital", "Risk management")
        for n in range(1, 4)
    ] + [_report_page(n, _sentences(8, prefix=names[n - 1]), "Sustainability", "Climate") for n in range(4, 7)]
    chunks = chunk_pages(pages, target_chars=800, max_chars=1000)

    risk = [c for c in chunks if c.page_end <= 3]
    assert risk and all(c.section == "Risk, funding & capital › Risk management" for c in risk)
    assert risk[0].embed_text.startswith("Risk, funding & capital › Risk management\n\n")
    assert chunks[-1].section == "Sustainability › Climate"
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 6
    for chunk in chunks:
        assert "Annual Report 2025" not in chunk.content
        assert "ACME Group" not in chunk.content
        assert "≡" not in chunk.content


def test_section_change_starts_a_new_chunk_without_overlap() -> None:
    pages = [
        _report_page(1, _sentences(6, prefix="Theta"), "Strategy", "Our strategy"),
        _report_page(2, _sentences(6, prefix="Beta"), "Strategy", "Our strategy"),
        _report_page(3, _sentences(6, prefix="Gamma"), "Sustainability", "Climate"),
        _report_page(4, _sentences(6, prefix="Delta"), "Sustainability", "Climate"),
    ]
    chunks = chunk_pages(pages, target_chars=2000, max_chars=2500)

    assert [c.section for c in chunks] == ["Strategy › Our strategy", "Sustainability › Climate"]
    assert chunks[1].content.startswith("Gamma")
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 2)
    assert (chunks[1].page_start, chunks[1].page_end) == (3, 4)


def test_sentence_across_page_break_is_rejoined() -> None:
    pages = [
        _report_page(1, _sentences(3) + " We will support", "Strategy", "Our strategy"),
        _report_page(2, "these transitions with finance. " + _sentences(3), "Strategy", "Our strategy"),
        _report_page(3, _sentences(3), "Strategy", "Our strategy"),
    ]
    chunks = chunk_pages(pages)
    assert len(chunks) == 1
    assert "We will support these transitions with finance." in chunks[0].content
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 3)


def test_markdown_headings_and_tables() -> None:
    page_one = (
        "# Sustainability\n\n## Climate targets\n\n"
        + _sentences(4, prefix="Climate")
        + "\n\n| Metric | 2025 | 2024 |\n|---|---|---|\n"
        + "\n".join(f"| Row {i} | {i}.0 | {i}.5 |" for i in range(40))
    )
    page_two = "## Own workforce\n\n" + _sentences(4, prefix="Workforce")
    chunks = chunk_pages(
        [PageText(1, page_one), PageText(2, page_two)],
        target_chars=600,
        max_chars=800,
        text_format="markdown",
    )

    sections = [c.section for c in chunks]
    assert sections[0] == "Sustainability › Climate targets"
    assert sections[-1] == "Sustainability › Own workforce"
    assert chunks[-1].content.startswith("## Own workforce")
    assert all(len(c.content) <= 800 for c in chunks)

    table_chunks = [c for c in chunks if "| Row " in c.content]
    assert len(table_chunks) > 1
    assert all(c.content.startswith("| Metric |") or "| Metric |" in c.content for c in table_chunks)


def test_extractor_header_is_section_hint_combined_with_headings() -> None:
    pages = [
        PageText(
            page_number=n,
            text=f"## Economic capital {n}\n\n" + _sentences(12, prefix=f"Page{n}"),
            header="≡ Risk, funding & capital } Key risk developments",
        )
        for n in range(1, 3)
    ]
    chunks = chunk_pages(pages, text_format="markdown")
    assert chunks[0].section == "Risk, funding & capital › Key risk developments › Economic capital 1"
    assert chunks[-1].section == "Risk, funding & capital › Key risk developments › Economic capital 2"
    assert all("≡" not in c.content for c in chunks)


def test_split_sentences_keeps_abbreviations() -> None:
    text = "Revenue grew, e.g. in retail. Costs fell by approx. 5%. Mr. Smith agreed."
    assert split_sentences(text) == ["Revenue grew, e.g. in retail.", "Costs fell by approx. 5%.", "Mr. Smith agreed."]
