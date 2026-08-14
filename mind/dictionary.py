from __future__ import annotations

import html
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from . import __version__


DATAMUSE_HOST = "api.datamuse.com"
WIKTIONARY_HOST = "en.wiktionary.org"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_DEFINITION_LENGTH = 360
_EDGE_PUNCTUATION = "\"'‘’“”.,;:!?()[]{}<>…«»"
_JOINERS = {"'", "’", "-", "‐", "‑"}
_PARTS_OF_SPEECH = {
    "n": "noun",
    "v": "verb",
    "adj": "adjective",
    "adv": "adverb",
    "u": "",
}


class DefinitionLookupError(RuntimeError):
    pass


@dataclass(frozen=True)
class DefinitionSense:
    part_of_speech: str
    definition: str


@dataclass(frozen=True)
class DefinitionResult:
    word: str
    pronunciation: str
    senses: tuple[DefinitionSense, ...]
    source_name: str
    source_url: str


class _SecureDefinitionRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {
            DATAMUSE_HOST,
            WIKTIONARY_HOST,
        }:
            raise urllib.error.URLError("Blocked an unsafe dictionary redirect.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SECURE_OPENER = urllib.request.build_opener(_SecureDefinitionRedirectHandler())


class _PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "sup"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "sup"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def normalize_selected_word(text: str) -> str | None:
    """Return one clean word, or None for phrases, sentences, and symbols."""
    if not isinstance(text, str):
        return None
    word = text.strip().strip(_EDGE_PUNCTUATION)
    if not word or len(word) > 48 or any(character.isspace() for character in word):
        return None
    for index, character in enumerate(word):
        if character.isalpha():
            continue
        if (
            character in _JOINERS
            and 0 < index < len(word) - 1
            and word[index - 1].isalpha()
            and word[index + 1].isalpha()
        ):
            continue
        return None
    return word if any(character.isalpha() for character in word) else None


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = _PlainTextParser()
    try:
        parser.feed(html.unescape(value))
        cleaned = " ".join("".join(parser.parts).split())
    except (ValueError, TypeError):
        cleaned = " ".join(value.split())
    if len(cleaned) > MAX_DEFINITION_LENGTH:
        cleaned = cleaned[: MAX_DEFINITION_LENGTH - 1].rstrip() + "…"
    return cleaned


def parse_datamuse_response(payload: object, requested_word: str) -> DefinitionResult | None:
    if not isinstance(payload, list):
        return None
    requested = requested_word.casefold()
    entry = next(
        (
            item
            for item in payload
            if isinstance(item, dict)
            and str(item.get("word", "")).casefold() == requested
        ),
        None,
    )
    if entry is None:
        return None

    tags = entry.get("tags", [])
    tags = tags if isinstance(tags, list) else []
    pronunciation = next(
        (
            str(tag)[9:].strip().strip("/")
            for tag in tags
            if isinstance(tag, str) and tag.startswith("ipa_pron:")
        ),
        "",
    ) or next(
        (
            str(tag)[5:].strip().strip("/")
            for tag in tags
            if isinstance(tag, str) and tag.startswith("pron:")
        ),
        "",
    )
    default_part = next(
        (
            _PARTS_OF_SPEECH.get(str(tag), "")
            for tag in tags
            if str(tag) in _PARTS_OF_SPEECH
        ),
        "",
    )
    raw_definitions = entry.get("defs", [])
    if not isinstance(raw_definitions, list):
        return None
    senses: list[DefinitionSense] = []
    seen: set[str] = set()
    for raw_definition in raw_definitions:
        if not isinstance(raw_definition, str):
            continue
        part_code, separator, raw_text = raw_definition.partition("\t")
        definition = _clean_text(raw_text if separator else part_code)
        normalized = definition.casefold()
        if not definition or normalized in seen:
            continue
        seen.add(normalized)
        senses.append(DefinitionSense(
            _PARTS_OF_SPEECH.get(part_code, default_part),
            definition,
        ))
        if len(senses) == 2:
            break
    if not senses:
        return None
    return DefinitionResult(
        word=str(entry.get("word") or requested_word),
        pronunciation=pronunciation,
        senses=tuple(senses),
        source_name="Datamuse · WordNet & Wiktionary",
        source_url="https://www.datamuse.com/api/",
    )


def parse_wiktionary_response(payload: object, requested_word: str) -> DefinitionResult | None:
    if not isinstance(payload, dict):
        return None
    entries = payload.get("en")
    if not isinstance(entries, list):
        return None
    senses: list[DefinitionSense] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        part = str(entry.get("partOfSpeech", "")).strip().lower()
        definitions = entry.get("definitions", [])
        if not isinstance(definitions, list):
            continue
        for item in definitions:
            if not isinstance(item, dict):
                continue
            definition = _clean_text(item.get("definition"))
            normalized = definition.casefold()
            if not definition or normalized in seen:
                continue
            seen.add(normalized)
            senses.append(DefinitionSense(part, definition))
            if len(senses) == 2:
                break
        if len(senses) == 2:
            break
    if not senses:
        return None
    page_url = "https://en.wiktionary.org/wiki/" + urllib.parse.quote(requested_word, safe="")
    return DefinitionResult(
        word=requested_word,
        pronunciation="",
        senses=tuple(senses),
        source_name="Wiktionary · CC BY-SA",
        source_url=page_url,
    )


def _read_json(url: str, timeout: float) -> object:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {DATAMUSE_HOST, WIKTIONARY_HOST}:
        raise DefinitionLookupError("The dictionary address is not trusted.")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"Mind/{__version__} word-definition-popup",
        },
    )
    with _SECURE_OPENER.open(request, timeout=timeout) as response:
        declared_length = response.headers.get("Content-Length")
        if declared_length and int(declared_length) > MAX_RESPONSE_BYTES:
            raise DefinitionLookupError("The dictionary response was too large.")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DefinitionLookupError("The dictionary response was too large.")
    return json.loads(raw.decode("utf-8"))


def _lookup_datamuse(word: str, timeout: float) -> DefinitionResult | None:
    query = urllib.parse.urlencode({
        "sp": word,
        "md": "dpr",
        "ipa": "1",
        "max": "1",
    })
    payload = _read_json(f"https://{DATAMUSE_HOST}/words?{query}", timeout)
    return parse_datamuse_response(payload, word)


def _lookup_wiktionary(word: str, timeout: float) -> DefinitionResult | None:
    safe_word = urllib.parse.quote(word, safe="")
    payload = _read_json(
        f"https://{WIKTIONARY_HOST}/api/rest_v1/page/definition/{safe_word}",
        timeout,
    )
    return parse_wiktionary_response(payload, word)


def lookup_definition(word: str, timeout: float = 4.5) -> DefinitionResult:
    clean_word = normalize_selected_word(word)
    if clean_word is None:
        raise DefinitionLookupError("Select exactly one word.")

    last_error: Exception | None = None
    for lookup in (_lookup_datamuse, _lookup_wiktionary):
        try:
            result = lookup(clean_word, timeout)
            if result is not None:
                return result
        except (
            DefinitionLookupError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc

    if last_error is not None:
        raise DefinitionLookupError("The definition service is unavailable right now.") from last_error
    raise DefinitionLookupError(f"No English definition was found for “{clean_word}”.")
