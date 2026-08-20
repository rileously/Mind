"""Reading a Maldivian identity card out of whatever OCR returns.

What arrives is not a form. It is a pile of lines in the order the recogniser
walked the card, mixed with Thaana it could not read, and on a photograph taken
on a table it may be sideways, which OCR returns as almost nothing at all.

The rule with teeth is in Numbers: a misread digit puts the wrong person on a
ticket and RTL checks the ID at the jetty, so a number that cannot be read as
six digits is refused rather than guessed at. Everything here is a best
reading, which is why the caller shows it to somebody before using it.
"""

import unittest

from mind.id_card import (
    Card,
    best_card,
    find_born,
    find_name,
    find_number,
    is_furniture,
    looks_like_a_name,
    parse_card,
    tidy_number,
)


# What Windows OCR gives for a card held straight, in the order it walks it.
UPRIGHT = """REPUBLIC OF MALDIVES
NATIONAL IDENTITY CARD
Number:
A433093
Name
Azaan Bin Ahmed Aslam
Date of Birth
18/07/2015
Sex
M
Address
Dhiggaagasdhoshuge
HDh. Naivaadhoo"""

# The same card with the label and value on one line, which also happens.
INLINE = """REPUBLIC OF MALDIVES
NATIONAL IDENTITY CARD
Number: A433093
Name Azaan Bin Ahmed Aslam
Date of Birth 18/07/2015
Sex M
Address Dhiggaagasdhoshuge, HDh. Naivaadhoo"""

# What Windows OCR actually returns for this card: it reads down the label
# column first and the value column second, so every label arrives before any
# value and the whole card can come back as a single line. Captured from a real
# run rather than imagined, because the label-beside-value assumption this
# breaks is the one the first version of the parser was built on.
COLUMN_ORDER = (
    "REPUBLIC OF MALDIVES NATIONAL IDENTITY CARD Number: Name Date of Birth "
    "Sex Address A433093 Azaan Bin Ahmed Aslam 18/07/2015 Dhiggaagasdhoshuge "
    "HDh. Naivaadhoo"
)

# Sideways: the recogniser finds the printing it can and drops the rest.
SIDEWAYS = """MALDIVES
||| |
1
1"""


class Numbers(unittest.TestCase):
    def test_the_number_is_found(self):
        self.assertEqual(find_number(UPRIGHT), "A433093")

    def test_a_letter_read_as_a_digit_is_put_back(self):
        # O for 0 and I for 1 are what actually happen.
        self.assertEqual(find_number("Number: A43O093"), "A430093")
        self.assertEqual(find_number("Number: A4330I3"), "A433013")

    def test_the_letter_is_read_and_not_assumed(self):
        self.assertEqual(find_number("Number: B123456"), "B123456")

    def test_a_date_is_not_mistaken_for_a_number(self):
        self.assertEqual(find_number("Date of Birth 18/07/2015"), "")

    def test_too_few_digits_is_not_a_number(self):
        self.assertEqual(find_number("A4330"), "")

    def test_nothing_readable_gives_nothing(self):
        self.assertEqual(find_number(""), "")
        self.assertEqual(find_number(SIDEWAYS), "")

    def test_a_candidate_that_is_not_six_digits_is_refused(self):
        # Refused rather than guessed: the jetty checks this number.
        self.assertEqual(tidy_number("A", "43ZZ93"), "")


class Names(unittest.TestCase):
    def test_the_name_below_its_label(self):
        self.assertEqual(find_name(UPRIGHT), "Azaan Bin Ahmed Aslam")

    def test_the_name_beside_its_label(self):
        self.assertEqual(find_name(INLINE), "Azaan Bin Ahmed Aslam")

    def test_the_card_s_own_printing_is_not_a_name(self):
        for line in ("REPUBLIC OF MALDIVES", "NATIONAL IDENTITY CARD", "Date of Birth"):
            self.assertTrue(is_furniture(line), line)
            self.assertFalse(looks_like_a_name(line), line)

    def test_a_line_with_digits_is_not_a_name(self):
        self.assertFalse(looks_like_a_name("18/07/2015"))
        self.assertFalse(looks_like_a_name("A433093"))

    def test_one_word_is_not_a_full_name(self):
        self.assertFalse(looks_like_a_name("Azaan"))

    def test_an_address_after_the_name_is_not_taken_instead(self):
        self.assertEqual(find_name(UPRIGHT), "Azaan Bin Ahmed Aslam")

    def test_the_name_when_every_label_arrives_before_every_value(self):
        # The layout OCR really produces. The label "Name" is nowhere near the
        # name, so it is found as the words printed after the number instead.
        self.assertEqual(find_name(COLUMN_ORDER), "Azaan Bin Ahmed Aslam")

    def test_the_address_is_not_taken_as_the_name(self):
        # It follows the date, and the run of words stops at the first digit.
        self.assertNotIn("Dhiggaagasdhoshuge", find_name(COLUMN_ORDER))

    def test_nothing_readable_gives_nothing(self):
        self.assertEqual(find_name(SIDEWAYS), "")
        self.assertEqual(find_name(""), "")


class Dates(unittest.TestCase):
    def test_the_date_of_birth_is_found(self):
        self.assertEqual(find_born(UPRIGHT), "18/07/2015")

    def test_a_single_digit_day_is_padded(self):
        self.assertEqual(find_born("Date of Birth 8/7/2015"), "08/07/2015")

    def test_no_date_is_not_an_error(self):
        self.assertEqual(find_born("Name Azaan"), "")


class WholeCards(unittest.TestCase):
    def test_a_straight_card_reads_completely(self):
        card = parse_card(UPRIGHT)
        self.assertEqual(card.name, "Azaan Bin Ahmed Aslam")
        self.assertEqual(card.number, "A433093")
        self.assertEqual(card.born, "18/07/2015")
        self.assertTrue(card.usable)

    def test_half_a_reading_is_not_usable(self):
        self.assertFalse(Card(name="Azaan Bin Ahmed Aslam").usable)
        self.assertFalse(Card(number="A433093").usable)
        self.assertFalse(Card().usable)

    def test_the_real_ocr_layout_reads_completely(self):
        card = parse_card(COLUMN_ORDER)
        self.assertEqual(card.name, "Azaan Bin Ahmed Aslam")
        self.assertEqual(card.number, "A433093")
        self.assertEqual(card.born, "18/07/2015")
        self.assertTrue(card.usable)

    def test_an_unreadable_photo_gives_an_empty_card(self):
        card = parse_card(SIDEWAYS)
        self.assertFalse(card.usable)


class Turning(unittest.TestCase):
    """A card on a table is sideways as often as not."""

    def test_the_turn_that_read_is_the_one_used(self):
        card = best_card([SIDEWAYS, UPRIGHT])
        self.assertTrue(card.usable)
        self.assertEqual(card.number, "A433093")

    def test_the_first_complete_reading_wins(self):
        card = best_card([UPRIGHT, INLINE])
        self.assertEqual(card.name, "Azaan Bin Ahmed Aslam")

    def test_a_reading_with_a_birthday_beats_one_without(self):
        thin = "Number: A433093\nName Azaan Bin Ahmed Aslam"
        card = best_card([thin, UPRIGHT])
        self.assertEqual(card.born, "18/07/2015")

    def test_half_a_reading_survives_when_nothing_is_complete(self):
        # Shown with the missing half asked for, rather than the whole thing
        # retyped from scratch.
        card = best_card([SIDEWAYS, "Number: A433093"])
        self.assertEqual(card.number, "A433093")
        self.assertFalse(card.usable)

    def test_nothing_readable_at_any_turn(self):
        card = best_card([SIDEWAYS, SIDEWAYS])
        self.assertFalse(card.usable)
        self.assertEqual(card.name, "")

    def test_no_readings_at_all(self):
        self.assertFalse(best_card([]).usable)


class ThePanel(unittest.TestCase):
    def test_a_complete_reading_offers_to_use_it(self):
        from mind.telegram_ui import build_card_keyboard, card_text

        card = Card(name="Azaan Bin Ahmed Aslam", number="A433093", born="18/07/2015")
        text = card_text(card)
        self.assertIn("Azaan Bin Ahmed Aslam", text)
        self.assertIn("A433093", text)
        # The check is the point of the panel existing.
        self.assertIn("Check both against the card", text)
        labels = [
            item["text"]
            for row in build_card_keyboard(card)["inline_keyboard"]
            for item in row
        ]
        self.assertTrue(any("Use this" in label for label in labels))

    def test_a_partial_reading_does_not_offer_to_use_it(self):
        from mind.telegram_ui import build_card_keyboard, card_text

        card = Card(number="A433093")
        self.assertIn("name did not come out", card_text(card))
        labels = [
            item["text"]
            for row in build_card_keyboard(card)["inline_keyboard"]
            for item in row
        ]
        self.assertFalse(any("Use this" in label for label in labels))
        # The missing half is the thing to offer, not the whole card again.
        self.assertTrue(any("Add the name" in label for label in labels))
        self.assertTrue(any("Type it all" in label for label in labels))

    def test_a_reading_on_its_own_says_it_would_have_worked(self):
        from mind.telegram_ui import card_reading_text

        card = Card(name="Azaan Bin Ahmed Aslam", number="A433093", born="18/07/2015")
        text = card_reading_text(card)
        self.assertIn("Azaan Bin Ahmed Aslam", text)
        self.assertIn("A433093", text)
        # The point of testing outside a booking: nothing happened.
        self.assertIn("Nothing was booked", text)

    def test_a_failed_reading_on_its_own_says_what_to_try(self):
        from mind.telegram_ui import card_reading_text

        text = card_reading_text(Card())
        self.assertIn("could not be read", text)
        self.assertIn("flat", text)

    def test_a_half_reading_on_its_own_shows_the_half(self):
        from mind.telegram_ui import card_reading_text

        text = card_reading_text(Card(number="A433093"))
        self.assertIn("A433093", text)
        self.assertIn("could not be read", text)

    def test_the_shortcut_is_mentioned_where_typing_is_asked_for(self):
        from mind.telegram_ui import ask_who_text

        for count in (1, 2):
            self.assertIn("photograph", ask_who_text(count).lower())


if __name__ == "__main__":
    unittest.main()


class RealCards(unittest.TestCase):
    """Layouts seen from actual photographs, not imagined ones.

    The first version of the parser read the number off a real card and lost
    the name, because a label landed between the two and the run of words was
    stopped there rather than restarted after it.
    """

    def test_a_label_between_the_number_and_the_name(self):
        text = (
            "REPUBLIC OF MALDIVES NATIONAL IDENTITY CARD Number: Name Sex "
            "Address A229095 Date of Birth Aishath Adam 06/08/1965 F "
            "Muniyaage HDh. Naivaadhoo"
        )
        card = parse_card(text)
        self.assertEqual(card.name, "Aishath Adam")
        self.assertEqual(card.number, "A229095")
        self.assertEqual(card.born, "06/08/1965")

    def test_a_name_recognised_before_the_number(self):
        text = (
            "REPUBLIC OF MALDIVES NATIONAL IDENTITY CARD Name Aishath Adam "
            "Number A229095 Date of Birth 06/08/1965"
        )
        self.assertEqual(parse_card(text).name, "Aishath Adam")

    def test_a_label_is_never_read_as_a_name(self):
        # "Date of Birth" is three capitalised words in a row, and "Date of"
        # was being taken as somebody's name.
        from mind.id_card import name_runs

        self.assertNotIn("Date of", name_runs("A229095 Date of Birth 06/08/1965"))

    def test_the_address_is_not_preferred_over_the_name(self):
        text = "A229095 Aishath Adam 06/08/1965 F Muniyaage HDh. Naivaadhoo"
        self.assertEqual(parse_card(text).name, "Aishath Adam")

    def test_a_two_word_name_is_enough(self):
        self.assertEqual(parse_card("Number A229095 Aishath Adam").name, "Aishath Adam")


class Diagnosing(unittest.TestCase):
    def test_a_failed_reading_shows_what_ocr_saw(self):
        from mind.telegram_ui import card_reading_text

        text = card_reading_text(Card(number="A229095"), "MALDIVES Number A229095")
        self.assertIn("What it saw", text)
        self.assertIn("MALDIVES Number A229095", text)

    def test_a_long_reading_is_cut_rather_than_sent_whole(self):
        from mind.telegram_ui import raw_reading

        self.assertLess(len(raw_reading("x " * 2000)), 900)

    def test_nothing_seen_adds_nothing(self):
        from mind.telegram_ui import raw_reading

        self.assertEqual(raw_reading(""), "")


class TruncatedNames(unittest.TestCase):
    """A name a surname short is worse than one that would not read at all.

    "Ali Shakir Hussain" went to RTL as "Ali Shakir" because a character of
    Thaana was stuck to the surname, and RTL refused the booking on an identity
    check that named nothing. The name sits between two lines of Thaana on
    every card, so this is the normal case, not a freak one.
    """

    def test_thaana_stuck_to_the_surname_does_not_drop_it(self):
        text = "A089744 Ali Shakir Hussain\u0787 01/01/1960"
        self.assertEqual(parse_card(text).name, "Ali Shakir Hussain")

    def test_a_stray_mark_does_not_drop_it(self):
        self.assertEqual(
            parse_card("A089744 Ali Shakir Hussain* 01/01/1960").name,
            "Ali Shakir Hussain",
        )

    def test_noise_in_front_does_not_drop_it(self):
        self.assertEqual(
            parse_card("A089744 Ali Shakir |Hussain 01/01/1960").name,
            "Ali Shakir Hussain",
        )

    def test_a_digit_inside_a_word_is_not_guessed_at(self):
        # "Hussa1n" is a misread letter. Repairing it would turn a wrong name
        # into a confident wrong name, so the reading is flagged instead.
        card = parse_card("A089744 Ali Shakir Hussa1n 01/01/1960")
        self.assertEqual(card.name, "Ali Shakir")
        self.assertTrue(card.unsure)

    def test_a_clean_card_is_not_flagged(self):
        card = parse_card("A433093 Azaan Bin Ahmed Aslam 18/07/2015")
        self.assertEqual(card.name, "Azaan Bin Ahmed Aslam")
        self.assertFalse(card.unsure)

    def test_the_panel_warns_when_a_name_may_be_short(self):
        from mind.telegram_ui import card_text

        text = card_text(Card(name="Ali Shakir", number="A089744", unsure=True))
        self.assertIn("may be missing", text)

    def test_the_panel_stays_quiet_when_it_is_sure(self):
        from mind.telegram_ui import card_text

        text = card_text(Card(name="Ali Shakir Hussain", number="A089744"))
        self.assertNotIn("may be missing", text)

    def test_salvage_refuses_a_word_that_is_mostly_rubbish(self):
        from mind.id_card import salvage

        self.assertEqual(salvage("|||"), "")
        self.assertEqual(salvage("A1"), "")
        self.assertEqual(salvage("Hussain"), "Hussain")


class Correcting(unittest.TestCase):
    """Fixing one wrong word without retyping the rest.

    A reading is usually wrong in one place - a surname with Thaana stuck to
    it, a digit that was a letter - so making somebody retype a name and a
    number because one of them lost its last word is how a shortcut stops
    being one.
    """

    def numbers(self):
        from mind.id_card import card_number

        return card_number

    def names(self):
        from mind.id_card import card_name

        return card_name

    def test_a_typed_number_is_taken_as_typed(self):
        self.assertEqual(self.numbers()("A375667"), "A375667")
        self.assertEqual(self.numbers()("a375667"), "A375667")
        self.assertEqual(self.numbers()(" A 375667 "), "A375667")

    def test_a_label_typed_in_front_is_forgiven(self):
        self.assertEqual(self.numbers()("Number: A375667"), "A375667")

    def test_a_typed_number_is_not_repaired(self):
        # A person looking at the card who types O meant O. Guessing here
        # would silently change a number somebody had just checked.
        self.assertEqual(self.numbers()("AO75667"), "")

    def test_things_that_are_not_numbers(self):
        for text in ("", "nope", "A37566", "A3756678", "Ali Shakir"):
            self.assertEqual(self.numbers()(text), "", text)

    def test_a_typed_name_is_kept_as_written(self):
        self.assertEqual(self.names()("Ali Shakir Hussain"), "Ali Shakir Hussain")
        self.assertEqual(self.names()("  Ali   Shakir  "), "Ali Shakir")

    def test_a_name_label_typed_in_front_is_forgiven(self):
        self.assertEqual(self.names()("Name: Ali Shakir Hussain"), "Ali Shakir Hussain")

    def test_things_that_are_not_names(self):
        for text in ("", "Ali", "A375667", "Ali 2", "x" * 70):
            self.assertEqual(self.names()(text), "", text)

    def test_the_edit_panel_says_which_half_is_kept(self):
        from mind.telegram_ui import card_edit_text

        card = Card(name="Ali Shakir", number="A089744")
        self.assertIn("number already read is kept", card_edit_text(card, "name"))
        self.assertIn("name already read is kept", card_edit_text(card, "id"))

    def test_the_edit_panel_shows_what_was_read(self):
        from mind.telegram_ui import card_edit_text

        card = Card(name="Ali Shakir", number="A089744")
        self.assertIn("Ali Shakir", card_edit_text(card, "name"))
        self.assertIn("A089744", card_edit_text(card, "id"))

    def test_a_complete_reading_still_offers_both_edits(self):
        from mind.telegram_ui import build_card_keyboard

        card = Card(name="Ali Shakir Hussain", number="A089744")
        labels = [
            item["text"]
            for row in build_card_keyboard(card)["inline_keyboard"]
            for item in row
        ]
        self.assertTrue(any("Name" in label for label in labels))
        self.assertTrue(any("ID number" in label for label in labels))
