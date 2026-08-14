from __future__ import annotations

import re
from dataclasses import dataclass


_TOKEN_RE = re.compile(r"([A-Za-z]{3,24})([.,!;:]?)$")
_WORD_RE = re.compile(r"[A-Za-z]{3,24}")
_UNSAFE_PREFIX_CHARACTERS = frozenset("./\\@_-#?$")
_UNSAFE_SUFFIX_CHARACTERS = frozenset("/\\@_-#$")

# Frequent errors that are ambiguous in a general frequency dictionary. Keeping this
# list explicit prevents examples such as "adress" -> "dress" and "helo" -> "help".
_SAFE_COMMON_CORRECTIONS = {
    "acommodate": "accommodate",
    "adress": "address",
    "becuase": "because",
    "definately": "definitely",
    "freind": "friend",
    "goverment": "government",
    "happend": "happened",
    "helo": "hello",
    "mispelled": "misspelled",
    "mispelling": "misspelling",
    "neccessary": "necessary",
    "occured": "occurred",
    "recieve": "receive",
    "seperate": "separate",
    "teh": "the",
    "thier": "their",
    "untill": "until",
    "wierd": "weird",
    "wrod": "word",
}

# Balanced mode covers common real-world typing slips where a frequency-only spellchecker
# either stays silent or chooses the wrong word. Every entry is reversible with Backspace.
_BALANCED_CORRECTIONS = {
    **_SAFE_COMMON_CORRECTIONS,
    "acheive": "achieve",
    "arguement": "argument",
    "begining": "beginning",
    "calender": "calendar",
    "comming": "coming",
    "crnt": "can't",
    "enviroment": "environment",
    "existance": "existence",
    "grammer": "grammar",
    "immediatly": "immediately",
    "knowlege": "knowledge",
    "lenght": "length",
    "occurence": "occurrence",
    "plase": "please",
    "prbkm": "problem",
    "prefered": "preferred",
    "publically": "publicly",
    "realy": "really",
    "remeber": "remember",
    "sentance": "sentence",
    "similiar": "similar",
    "succesful": "successful",
    "thar": "that",
    "tommorow": "tomorrow",
    "truely": "truly",
    "usualy": "usually",
    "writting": "writing",
}

AUTOCORRECT_STRENGTHS = ("conservative", "balanced", "strong")


@dataclass(frozen=True)
class CompletedToken:
    word: str
    punctuation: str = ""

    @property
    def text(self) -> str:
        return self.word + self.punctuation


@dataclass(frozen=True)
class TailCorrection:
    original: str
    corrected: str


def completed_token(text_before_space: str) -> CompletedToken | None:
    """Return the plain English word immediately before a newly typed space.

    URL, email, file-path, identifier, command, and numeric fragments are rejected.
    One trailing punctuation mark is retained so it survives replacement.
    """
    match = _TOKEN_RE.search(text_before_space)
    if not match:
        return None
    start = match.start(1)
    if start and text_before_space[start - 1] in _UNSAFE_PREFIX_CHARACTERS:
        return None
    if start and text_before_space[start - 1].isalnum():
        return None
    return CompletedToken(match.group(1), match.group(2))


def _restore_case(original: str, replacement: str) -> str:
    if original.islower():
        return replacement
    if original[0].isupper() and original[1:].islower():
        return replacement.capitalize()
    return replacement


class LocalAutocorrect:
    """Offline English spelling suggestions with selectable correction strength."""

    def __init__(self) -> None:
        from spellchecker import SpellChecker

        self._spell_checker_type = SpellChecker
        self._spellers = {1: SpellChecker(language="en", distance=1)}

    def suggest(self, word: str, strength: str = "balanced") -> str | None:
        if not isinstance(word, str) or not (3 <= len(word) <= 24):
            return None
        if not word.isascii() or not word.isalpha():
            return None
        if word.isupper() or (not word.islower() and not word.istitle()):
            return None

        if strength not in AUTOCORRECT_STRENGTHS:
            strength = "balanced"

        lower = word.lower()
        corrections = (
            _SAFE_COMMON_CORRECTIONS if strength == "conservative" else _BALANCED_CORRECTIONS
        )
        common = corrections.get(lower)
        if common:
            return _restore_case(word, common)

        # Capitalized unknown words are commonly names. Only the explicit high-confidence
        # table above may change one (for example, "Teh" at the start of a sentence).
        if word.istitle():
            return None
        distance = 2 if strength == "strong" else 1
        speller = self._spellers.get(distance)
        if speller is None:
            speller = self._spell_checker_type(language="en", distance=distance)
            self._spellers[distance] = speller

        if lower in speller:
            return None

        candidates = speller.candidates(lower)
        if not candidates:
            return None
        ranked = sorted(
            ((candidate, speller.word_frequency[candidate]) for candidate in candidates),
            key=lambda item: (-item[1], item[0]),
        )
        best, best_frequency = ranked[0]
        minimum_frequency = 1_000 if strength == "conservative" else (500 if strength == "balanced" else 100)
        if best == lower or best_frequency < minimum_frequency:
            return None

        # Require a large lead over the next guess. This rejects uncertain corrections
        # such as "adress", whose raw dictionary preference is the unrelated "dress".
        second_frequency = ranked[1][1] if len(ranked) > 1 else 0
        confidence_ratio = 8 if strength == "conservative" else (3 if strength == "balanced" else 5)
        if second_frequency and best_frequency < second_frequency * confidence_ratio:
            return None
        if not best.isascii() or not all(character.isalpha() or character == "'" for character in best):
            return None
        return best

    def correct_tail(
        self,
        text_before_space: str,
        strength: str = "balanced",
        max_characters: int = 80,
    ) -> TailCorrection | None:
        """Correct misspellings in the latest safe phrase, not just its last word.

        This catches up when a fast typist presses the next key before the previous
        correction can be inserted. The span is bounded and never crosses a sentence,
        line, or tab boundary.
        """
        if not text_before_space or max_characters < 3:
            return None

        start = max(text_before_space.rfind("\n"), text_before_space.rfind("\t")) + 1
        line_start = start
        for sentence_break in re.finditer(r"[.!?]\s+", text_before_space[line_start:]):
            start = line_start + sentence_break.end()
        if len(text_before_space) - start > max_characters:
            start = len(text_before_space) - max_characters
            next_space = text_before_space.find(" ", start)
            if next_space < 0:
                return None
            start = next_space + 1

        original = text_before_space[start:]
        pieces: list[str] = []
        cursor = 0
        changed = False
        for match in _WORD_RE.finditer(original):
            word_start, word_end = match.span()
            before = original[word_start - 1] if word_start else ""
            after = original[word_end] if word_end < len(original) else ""
            replacement = None
            dot_inside_token = after == "." and word_end + 1 < len(original) \
                and original[word_end + 1].isalnum()
            if before not in _UNSAFE_PREFIX_CHARACTERS \
                    and after not in _UNSAFE_SUFFIX_CHARACTERS and not dot_inside_token:
                replacement = self.suggest(match.group(0), strength)
            pieces.append(original[cursor:word_start])
            pieces.append(replacement or match.group(0))
            changed = changed or replacement is not None
            cursor = word_end
        pieces.append(original[cursor:])
        corrected = "".join(pieces)
        if not changed or corrected == original:
            return None
        return TailCorrection(original, corrected)
