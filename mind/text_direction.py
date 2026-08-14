from __future__ import annotations

import unicodedata
import re


RTL_MARK = "\u200f"
_LEADING_DIRECTIONAL_CONTROLS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_URL_OR_EMAIL = re.compile(r"(?:https?://|www\.)\S+|\b\S+@\S+\b", re.IGNORECASE)

DHIVEHI_RETRY_PROMPT = (
    "Translate the input into fluent modern standard Dhivehi. STRICT OUTPUT CONTRACT: "
    "return only the finished translation using Thaana letters from Unicode U+0780 through "
    "U+07BF, with no English commentary, "
    "analysis, alternatives, labels, arrows, quotations, transliteration, or explanation. "
    "Arabic, Bengali, Devanagari, and every other writing system are forbidden. Convert "
    "Arabic words and "
    "greetings into their standard Thaana spelling. Example: the Arabic السلام عليكم must "
    "be written as އައްސަލާމު ޢަލައިކުމް. "
    "The Bengali হ্যালো is forbidden; write ހެލޯ instead. "
    "Never describe how you chose the translation. Preserve names, numbers, URLs, paragraph "
    "breaks, and meaning. Begin directly with the Dhivehi translation."
)

_COMMON_DHIVEHI_TRANSLATIONS = {
    "hello": "ހެލޯ",
    "hi": "ހެލޯ",
    "hey": "ހެލޯ",
    "hey there": "ހެލޯ",
    "salaam alaikum": "އައްސަލާމު ޢަލައިކުމް",
    "salam alaikum": "އައްސަލާމު ޢަލައިކުމް",
    "assalamu alaikum": "އައްސަލާމު ޢަލައިކުމް",
    "as-salamu alaikum": "އައްސަލާމު ޢަލައިކުމް",
}


def contains_thaana(text: str) -> bool:
    return any("\u0780" <= character <= "\u07bf" for character in text)


def contains_arabic_script(text: str) -> bool:
    arabic_ranges = (
        ("\u0600", "\u077f"),
        ("\u08a0", "\u08ff"),
        ("\ufb50", "\ufdff"),
        ("\ufe70", "\ufeff"),
    )
    return any(
        any(start <= character <= end for start, end in arabic_ranges)
        and unicodedata.category(character)[0] in {"L", "M"}
        for character in text
    )


def contains_foreign_script(text: str) -> bool:
    """Detect letters or combining marks outside Thaana and Latin source names."""
    for character in text:
        category = unicodedata.category(character)
        if category[0] not in {"L", "M"}:
            continue
        if "\u0780" <= character <= "\u07bf":
            continue
        if category[0] == "L" and "LATIN" in unicodedata.name(character, ""):
            continue
        return True
    return False


def is_dhivehi_trigger(trigger: str) -> bool:
    normalized = trigger.strip().casefold()
    return normalized == "dhivehi" or normalized in {"translate:dv", "translate:div"}


def common_dhivehi_translation(source: str) -> str | None:
    """Return stable translations for short greetings that small models often mangle."""
    stripped = source.strip()
    punctuation = ""
    if stripped and stripped[-1] in ".!?":
        punctuation = "؟" if stripped[-1] == "?" else stripped[-1]
        stripped = stripped[:-1].rstrip()
    key = re.sub(r"\s+", " ", stripped).casefold()
    translated = _COMMON_DHIVEHI_TRANSLATIONS.get(key)
    return translated + punctuation if translated else None


def is_clean_dhivehi_translation(result: str, source: str) -> bool:
    """Reject missing Thaana and English meta-commentary leaked by small AI models."""
    if (
        not contains_thaana(result)
        or contains_arabic_script(result)
        or contains_foreign_script(result)
    ):
        return False
    lowered = result.casefold()
    if any(marker in lowered for marker in (
        "->", "→", "thaana:", "dhivehi:", "translation:", "let's use",
        "depending on context", "friendly equivalent", "how about", "wait,",
    )):
        return False

    source_words = {word.casefold() for word in _LATIN_WORD.findall(_URL_OR_EMAIL.sub("", source))}
    output_without_links = _URL_OR_EMAIL.sub("", result)
    unexpected = [
        word for word in _LATIN_WORD.findall(output_without_links)
        if word.casefold() not in source_words
    ]
    return len(unexpected) <= 1


def prepare_dhivehi_output(text: str) -> str:
    """Normalize Thaana without leaving hidden direction characters in user text."""
    normalized = unicodedata.normalize("NFC", text)
    return "\n".join(
        line.lstrip(_LEADING_DIRECTIONAL_CONTROLS)
        for line in normalized.split("\n")
    )
