"""Reading a Maldivian identity card, so a ticket is a photo rather than typing.

The two things RTL wants - a full name and a national ID - are both printed on
the card in the wallet of the person travelling. Typing them into a phone is
the slowest part of a booking and the one with a wrong answer at the end of it,
so the card is photographed instead and read here.

What comes back from OCR is not a form. It is a pile of lines in whatever order
the recogniser walked the card, mixed with Thaana it cannot read, the coat of
arms, and the word MALDIVES twice. So nothing here trusts position: the number
is found by its shape, and the name by the label next to it, with every line
that is obviously furniture ruled out first.

Nothing is ever taken as certain. A misread digit puts the wrong person on a
ticket, so this returns its best reading and the caller shows it to somebody
before it is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# A Maldivian national ID is a letter and six digits: A433093. The letter is
# nearly always A, but it is read rather than assumed.
NUMBER = re.compile(r"\b([A-Za-z])[\s.-]?([0-9OoIlSs]{6})\b")

BORN = re.compile(r"\b([0-3]?[0-9])[/.-]([01]?[0-9])[/.-]((?:19|20)[0-9]{2})\b")

# Everything printed on every card. A line that is one of these is furniture
# rather than somebody's name, however much it looks like words.
FURNITURE = (
    "republic of maldives",
    "national identity card",
    "identity card",
    "maldives",
    "number",
    "name",
    "date of birth",
    "birth",
    "sex",
    "address",
    "male",
    "female",
    "permanent address",
    "card number",
    "signature",
    "issued",
    "expiry",
    "valid",
)

# The same printing broken into single words, because a run of words is
# scanned one at a time and "Date of Birth" arrives as three of them. Without
# this, "Date of" is two capitalised words in a row and reads as a name.
FURNITURE_WORDS = frozenset(
    "republic of maldives national identity card number name date birth sex "
    "address male female permanent signature issued expiry valid dob "
    "sign holder authority".split()
)

# What a name is allowed to be made of. Apostrophes and hyphens occur; digits
# do not, and a line containing one is a date or an address.
NAME_OK = re.compile(r"^[A-Za-z][A-Za-z .'-]{3,59}$")

# Digits misread as letters. Only the three that actually happen, because a
# generous table here would turn a readable number into a wrong one.
DIGIT_FIXES = {"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "s": "5"}


@dataclass(frozen=True)
class Card:
    """What was read off the card, and how sure the reading is."""

    name: str = ""
    number: str = ""
    born: str = ""
    # True when a word with letters in it was dropped right after the name.
    # The reading may be a surname short, which is worse than unreadable.
    unsure: bool = False

    @property
    def usable(self) -> bool:
        """Both halves present, which is what a ticket needs."""
        return bool(self.name and self.number)


def tidy_number(letter: str, digits: str) -> str:
    """A candidate number with the usual misreadings put back."""
    fixed = "".join(DIGIT_FIXES.get(character, character) for character in digits)
    if not fixed.isdigit():
        return ""
    return f"{letter.upper()}{fixed}"


def find_number(text: str) -> str:
    """The national ID, by its shape rather than by where it sits.

    The first match wins because the number is printed above the name on every
    card, and nothing else on one has this shape - a date has separators, and
    an address has words.
    """
    for match in NUMBER.finditer(text or ""):
        number = tidy_number(match.group(1), match.group(2))
        if number:
            return number
    return ""


def find_born(text: str) -> str:
    """The date of birth, as it is printed: dd/mm/yyyy."""
    match = BORN.search(text or "")
    if not match:
        return ""
    day, month, year = match.groups()
    return f"{int(day):02d}/{int(month):02d}/{year}"


def is_furniture(line: str) -> bool:
    lowered = line.strip().lower().strip(":. ")
    if not lowered:
        return True
    return any(lowered == word or lowered.startswith(word) for word in FURNITURE)


def looks_like_a_name(line: str) -> bool:
    """Two or more words of letters, and nothing that belongs to a form."""
    candidate = line.strip().strip(":.")
    if not candidate or is_furniture(candidate):
        return False
    if not NAME_OK.match(candidate):
        return False
    return len(candidate.split()) >= 2


def find_name(text: str) -> str:
    """The full name, taken from beside or below the label that announces it.

    Both layouts happen. The recogniser sometimes keeps "Name" and the name on
    one line and sometimes splits them, and on a card photographed at an angle
    the label can arrive several lines early - so the label is found first and
    the next thing that looks like a name is taken, wherever it landed.
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    for at, line in enumerate(lines):
        stripped = line.strip().strip(":")
        lowered = stripped.lower()
        if not lowered.startswith("name"):
            continue
        # "Name Azaan Bin Ahmed Aslam" on one line.
        rest = stripped[4:].strip(" :.")
        if looks_like_a_name(rest):
            return rest
        # "Name" alone, with the name on one of the lines after it.
        for following in lines[at + 1 : at + 4]:
            if looks_like_a_name(following):
                return following.strip().strip(":.")
    found = name_after_number(text)
    if found:
        return found
    # No label survived the recognition; fall back to the first line that
    # reads like a name and is not part of the card's own printing.
    for line in lines:
        if looks_like_a_name(line):
            return line.strip().strip(":.")
    # Last resort: the first run of plain words anywhere on the card. The name
    # is printed above the address, so on a card whose number was recognised
    # after its name this still finds the person rather than their house.
    runs = name_runs(text)
    return runs[0] if runs else ""


def name_after_number(text: str) -> str:
    """The name as the words printed just after the ID number.

    Windows OCR reads this card in column order: every label first, then every
    value, so "Name" arrives nowhere near the name and the whole card can come
    back as one line. What survives that is the order the values themselves are
    printed in - number, then name, then date of birth - so the name is the run
    of words between the number and the first thing containing a digit.
    """
    match = NUMBER.search(text or "")
    if not match or not tidy_number(match.group(1), match.group(2)):
        return ""
    runs = name_runs(text[match.end():])
    return runs[0] if runs else ""


def name_runs(text: str) -> list:
    """Every stretch of words that could be somebody's name.

    A run ends at anything with a digit in it, and a label inside the values
    breaks one run and starts another rather than ending the search - which is
    the whole point. The order the recogniser emits a card's values in varies
    with the layout, and on some cards "Date of Birth" lands between the number
    and the name. Stopping there found nothing at all.
    """
    return [run for run, _cut in name_runs_cut(text)]


def salvage(word: str) -> str:
    """A word with the rubbish stuck to it taken off.

    The name on a card sits between two lines of Thaana, and a recogniser that
    cannot read Thaana still sometimes attaches a character of it to the word
    next door - as do the holograms, the laminate creases and the edge of the
    box the name is printed in. "Hussain" arriving as "Hussain<thaana>" was
    dropping a surname silently, which is worse than failing to read it.

    A word with a digit in it is not salvaged. "Hussa1n" is a misread letter
    and guessing which one turns a wrong name into a confident wrong name.
    """
    if any(character.isdigit() for character in word):
        return ""
    cleaned = "".join(c for c in word if c.isascii() and (c.isalpha() or c in "'-"))
    cleaned = cleaned.strip("'-")
    return cleaned if sum(c.isalpha() for c in cleaned) >= 2 else ""


def name_runs_cut(text: str):
    """Every possible name, and whether something name-like was dropped after it.

    The flag is the useful half. A run that ended because the next word had
    letters in it may be a name with its last part missing, and a truncated
    name is worse than none: it is confidently wrong, and it goes on a ticket.
    """
    found: list = []
    words: list[str] = []

    def keep(cut: bool):
        if len(words) >= 2:
            found.append((" ".join(words), cut))

    for raw in (text or "").replace("\n", " ").split():
        word = raw.strip(",;:.")
        if not word:
            continue
        if word.lower().strip("'-") in FURNITURE_WORDS:
            keep(False)
            words = []
            continue
        if re.match(r"^[A-Za-z][A-Za-z'-]*$", word):
            words.append(word)
            if len(words) >= 5:
                keep(False)
                words = []
            continue
        rescued = salvage(word)
        if rescued and rescued.lower() not in FURNITURE_WORDS:
            words.append(rescued)
            if len(words) >= 5:
                keep(False)
                words = []
            continue
        # Not a word and not rescuable. If it had letters in it, something that
        # might have belonged to the name has just been thrown away.
        keep(any(c.isalpha() for c in word))
        words = []
    keep(False)
    return found


def parse_card(text: str) -> Card:
    """Everything worth having off one reading of a card."""
    name = find_name(text)
    return Card(
        name=name,
        number=find_number(text),
        born=find_born(text),
        unsure=name_was_cut(text, name),
    )


def name_was_cut(text: str, name: str) -> bool:
    """Whether the name that was chosen had something dropped after it."""
    if not name:
        return False
    for run, cut in name_runs_cut(text):
        if run == name:
            return bool(cut)
    return False


def best_card(readings) -> Card:
    """The best of several readings of the same card.

    A photograph of a card on a table is rarely upright, and the recogniser
    reads sideways text as nothing at all - so the same image is read at four
    turns and the turn that produced both halves is the one that was the right
    way up. Ties go to the earlier reading, which is the least rotated.
    """
    best = Card()
    for text in readings or ():
        card = parse_card(text)
        if card.usable:
            if not best.usable:
                best = card
            elif card.born and not best.born:
                best = card
        elif not best.usable and (card.name or card.number):
            # Half a reading is still better than nothing: it gets shown with
            # the missing half asked for rather than the whole thing retyped.
            if not (best.name or best.number):
                best = card
    return best


def card_number(typed: str) -> str:
    """An ID number as a person types it, or "" if that was not one.

    Typed by somebody looking at the card, so it is taken more literally than
    a reading is: no letter-for-digit repairs, because a person who types O
    meant O. Spaces and a stray "Number:" in front are forgiven.
    """
    text = (typed or "").strip()
    for word in ("number", "id", "no"):
        if text.lower().startswith(word):
            text = text[len(word):].lstrip(" :.")
    text = text.replace(" ", "").replace("-", "").upper()
    if re.fullmatch(r"[A-Z][0-9]{6}", text):
        return text
    return ""


def card_name(typed: str) -> str:
    """A full name as a person types it, or "" if that was not one.

    Anything with two words of letters counts. A card carries names this code
    has never seen and should not be the judge of, so the test is shape alone:
    it must not be a number, a date, or one word.
    """
    text = " ".join((typed or "").split())
    for word in ("name", "full name"):
        if text.lower().startswith(word):
            text = text[len(word):].lstrip(" :.")
    text = " ".join(text.split())
    if not text or any(character.isdigit() for character in text):
        return ""
    if len(text.split()) < 2 or len(text) > 60:
        return ""
    if not NAME_OK.match(text):
        return ""
    return text
