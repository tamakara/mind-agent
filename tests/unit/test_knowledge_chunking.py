from workhub.knowledge.chunking import split_document


def test_markdown_chunking_preserves_heading_path_and_source_lines() -> None:
    content = "# Leave Policy\nIntro\n## Eligibility\nEmployees qualify.\nMore detail.\n"

    chunks = split_document("policies/leave.md", content)

    assert [chunk.heading_path for chunk in chunks] == [
        "Leave Policy",
        "Leave Policy > Eligibility",
    ]
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert chunks[1].line_start == 3
    assert chunks[1].line_end == 5


def test_txt_chunking_is_recursive_and_keeps_line_bounds() -> None:
    content = "line one\n" + "word " * 500 + "\nlast line"

    chunks = split_document("guide.txt", content)

    assert len(chunks) > 1
    assert chunks[0].line_start == 1
    assert chunks[-1].line_end == 3
    assert all(chunk.heading_path is None for chunk in chunks)
