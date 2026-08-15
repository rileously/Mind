from __future__ import annotations

import re


COMMON_PATTERNS: list[tuple[str, str]] = [
    ("looking forward to", " hearing from you soon."),
    ("please let me know if", " you have any questions or need anything else."),
    ("let me know if you", " need any further details."),
    ("thank you for your", " time and assistance."),
    ("thanks in advance for", " your help and support."),
    ("feel free to reach", " out if you have any questions."),
    ("i hope this email finds", " you well and having a great week."),
    ("i hope you are doing", " well today."),
    ("as discussed in our", " previous conversation,"),
    ("attached is the", " updated file for your review."),
    ("have a great", " rest of your day!"),
    ("have a wonderful", " weekend ahead!"),
    ("let's schedule a", " quick meeting to discuss this further."),
    ("just following up on", " our earlier discussion regarding this."),
    ("could you please provide", " an update when you have a moment?"),
    ("sorry for the delay in", " getting back to you."),
    ("if you have any questions,", " please don't hesitate to ask."),
    ("at your earliest", " convenience."),
    ("don't hesitate to", " reach out if needed."),
    ("best", " regards,"),
    ("warm", " regards,"),
    ("kind", " regards,"),
]


def get_local_smart_completion(context_text: str) -> str | None:
    """Return an instant smart continuation for common sentence prefixes."""
    if not context_text:
        return None

    cleaned = context_text.strip().lower()
    # Normalize multiple whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Check for trailing matches
    for pattern, continuation in COMMON_PATTERNS:
        pat_clean = pattern.lower()
        if cleaned.endswith(pat_clean):
            return continuation
        # Check if user typed part of the phrase (at least 6 chars)
        if len(cleaned) >= 6 and pat_clean.startswith(cleaned[-len(pat_clean) :]):
            # calculate remaining part of pattern + continuation
            match_len = len(cleaned[-len(pat_clean) :])
            remaining_pattern = pattern[match_len:]
            return remaining_pattern + continuation

    return None


def suggest_sentence_completion(context_text: str) -> str | None:
    """Analyze the trailing context and suggest a natural smart completion."""
    if not context_text or len(context_text.strip()) < 3:
        return None

    # First check instant offline rules
    local_suggestion = get_local_smart_completion(context_text)
    if local_suggestion:
        return local_suggestion

    # Heuristic fallback for common unfinished punctuation
    trimmed = context_text.rstrip()
    if trimmed.endswith("e.g."):
        return " for example,"
    if trimmed.endswith("i.e."):
        return " that is to say,"

    return None
