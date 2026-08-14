from __future__ import annotations

import math
import re
from dataclasses import dataclass


class TextToolError(ValueError):
    pass


@dataclass(frozen=True)
class TextTool:
    trigger: str
    label: str
    description: str


@dataclass(frozen=True)
class TextToolResult:
    text: str
    message: str
    replace: bool = True


LOCAL_TEXT_TOOLS = (
    TextTool(
        "local-clean-spacing",
        "Clean spacing",
        "Remove repeated spaces, trailing whitespace, and excess blank lines locally.",
    ),
    TextTool(
        "local-bullets",
        "Lines to bullets",
        "Turn two or more selected lines into a clean Markdown bullet list locally.",
    ),
    TextTool(
        "local-dedupe-lines",
        "Remove duplicate lines",
        "Keep the first occurrence of each selected line and preserve its order locally.",
    ),
    TextTool(
        "local-uppercase",
        "UPPERCASE",
        "Convert selected text to uppercase locally.",
    ),
    TextTool(
        "local-lowercase",
        "lowercase",
        "Convert selected text to lowercase locally.",
    ),
    TextTool(
        "local-writing-stats",
        "Writing statistics",
        "Count words, characters, sentences, paragraphs, and estimated reading time locally.",
    ),
)
LOCAL_TEXT_TOOL_BY_TRIGGER = {tool.trigger: tool for tool in LOCAL_TEXT_TOOLS}


_HORIZONTAL_SPACE = re.compile(r"[^\S\r\n]+")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
_LIST_PREFIX = re.compile(r"^\s*(?:(?:[-*\u2022\u2023\u25aa])|(?:\d+[.)]))\s+")
_WORD = re.compile(r"\b[^\W_]+(?:['\u2019-][^\W_]+)*\b", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?]+(?:[\"'\u2019\u201d)\]]*)?(?=\s|$)")


def run_text_tool(trigger: str, text: str) -> TextToolResult:
    if trigger not in LOCAL_TEXT_TOOL_BY_TRIGGER:
        raise TextToolError("This local text tool is not available.")
    if not text or not text.strip():
        raise TextToolError("Select some text before using this tool.")

    if trigger == "local-clean-spacing":
        return _clean_spacing(text)
    if trigger == "local-bullets":
        return _lines_to_bullets(text)
    if trigger == "local-dedupe-lines":
        return _deduplicate_lines(text)
    if trigger == "local-uppercase":
        return _case_result(text.upper(), text, "Text converted to uppercase.")
    if trigger == "local-lowercase":
        return _case_result(text.lower(), text, "Text converted to lowercase.")
    if trigger == "local-writing-stats":
        return _writing_statistics(text)
    raise TextToolError("This local text tool is not available.")


def _clean_spacing(text: str) -> TextToolResult:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.translate(
        {
            ord("\u00a0"): " ",
            ord("\u2007"): " ",
            ord("\u202f"): " ",
        }
    )
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in normalized.split("\n")]
    cleaned = _EXCESS_BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    if not cleaned:
        raise TextToolError("The selection contains only whitespace.")
    message = "Spacing cleaned." if cleaned != text else "Spacing is already clean."
    return TextToolResult(cleaned, message)


def _lines_to_bullets(text: str) -> TextToolResult:
    lines = [line.strip() for line in _normalized_lines(text) if line.strip()]
    if len(lines) < 2:
        raise TextToolError("Select two or more lines to create a bullet list.")
    items = [_LIST_PREFIX.sub("", line).strip() for line in lines]
    if any(not item for item in items):
        raise TextToolError("One of the selected lines does not contain text.")
    return TextToolResult("\n".join(f"- {item}" for item in items), "Lines converted to bullets.")


def _deduplicate_lines(text: str) -> TextToolResult:
    lines = _normalized_lines(text)
    nonempty = [line for line in lines if line.strip()]
    if len(nonempty) < 2:
        raise TextToolError("Select two or more lines to remove duplicates.")
    unique: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if not line.strip():
            unique.append(line)
            continue
        key = line.strip().casefold()
        if key not in seen:
            seen.add(key)
            unique.append(line)
    removed = len(nonempty) - len(seen)
    message = f"Removed {removed} duplicate line{'s' if removed != 1 else ''}."
    if not removed:
        message = "No duplicate lines found."
    return TextToolResult("\n".join(unique), message)


def _case_result(result: str, original: str, changed_message: str) -> TextToolResult:
    message = changed_message if result != original else "The selected text already uses that case."
    return TextToolResult(result, message)


def _writing_statistics(text: str) -> TextToolResult:
    words = len(_WORD.findall(text))
    characters = len(text)
    sentences = len(_SENTENCE_END.findall(text))
    if words and not sentences:
        sentences = 1
    paragraphs = len([part for part in re.split(r"(?:\r?\n){2,}", text) if part.strip()])
    minutes = math.ceil(words / 200) if words else 0
    message = (
        f"{words} word{'s' if words != 1 else ''} \u00b7 "
        f"{characters} character{'s' if characters != 1 else ''} \u00b7 "
        f"{sentences} sentence{'s' if sentences != 1 else ''} \u00b7 "
        f"{paragraphs} paragraph{'s' if paragraphs != 1 else ''} \u00b7 "
        f"~{minutes} min read"
    )
    return TextToolResult(text, message, replace=False)


def _normalized_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
