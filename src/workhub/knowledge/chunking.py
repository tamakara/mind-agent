import bisect
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from langchain_text_splitters import RecursiveCharacterTextSplitter

from workhub.domain.knowledge import KnowledgeChunk

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class _Section:
    heading_path: str | None
    start: int
    text: str


def split_document(relative_path: str, content: str) -> list[KnowledgeChunk]:
    sections = (
        _markdown_sections(content)
        if PurePosixPath(relative_path).suffix.lower() == ".md"
        else [_Section(None, 0, content)]
    )
    line_offsets = _line_offsets(content)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1_000,
        chunk_overlap=100,
        separators=["\n\n", "\n", "。", ". ", " ", ""],
        add_start_index=True,
    )
    chunks: list[KnowledgeChunk] = []
    for section in sections:
        for document in splitter.create_documents([section.text]):
            text = document.page_content
            if not text.strip():
                continue
            local_start = int(document.metadata.get("start_index", 0))
            start = section.start + local_start
            end = start + len(text)
            chunks.append(
                KnowledgeChunk(
                    content=text,
                    heading_path=section.heading_path,
                    line_start=_line_number(line_offsets, start),
                    line_end=_line_number(line_offsets, max(start, end - 1)),
                )
            )
    return chunks


def _markdown_sections(content: str) -> list[_Section]:
    lines = content.splitlines(keepends=True)
    headings: list[str] = []
    sections: list[_Section] = []
    buffer: list[str] = []
    buffer_start = 0
    offset = 0
    current_path: str | None = None
    for line in lines:
        match = HEADING.match(line.rstrip("\r\n"))
        if match:
            if buffer:
                sections.append(_Section(current_path, buffer_start, "".join(buffer)))
            level = len(match.group(1))
            headings = headings[: level - 1]
            headings.append(match.group(2).strip())
            current_path = " > ".join(headings)
            buffer = [line]
            buffer_start = offset
        else:
            if not buffer:
                buffer_start = offset
            buffer.append(line)
        offset += len(line)
    if buffer or not sections:
        sections.append(_Section(current_path, buffer_start, "".join(buffer)))
    return sections


def _line_offsets(content: str) -> list[int]:
    offsets = [0]
    offsets.extend(index + 1 for index, char in enumerate(content) if char == "\n")
    return offsets


def _line_number(offsets: list[int], position: int) -> int:
    return bisect.bisect_right(offsets, position)
