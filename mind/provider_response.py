from __future__ import annotations


def extract_gemini_text(data: object) -> str:
    """Return final Gemini text parts while excluding any thinking/reasoning parts."""
    if not isinstance(data, dict):
        return ""
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return ""
    content = candidates[0].get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""

    final_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("thought") is True:
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            final_parts.append(text.strip())
    return "\n".join(final_parts).strip()
