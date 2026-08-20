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
        self.assertTrue(any("Type it instead" in label for label in labels))

    def test_the_shortcut_is_mentioned_where_typing_is_asked_for(self):
        from mind.telegram_ui import ask_who_text

        for count in (1, 2):
            self.assertIn("photograph", ask_who_text(count).lower())


if __name__ == "__main__":
    unittest.main()
