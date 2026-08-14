import unittest

from mind.text_direction import (
    common_dhivehi_translation,
    contains_arabic_script,
    contains_foreign_script,
    contains_thaana,
    is_clean_dhivehi_translation,
    is_dhivehi_trigger,
    prepare_dhivehi_output,
)


class TextDirectionTests(unittest.TestCase):
    def test_detects_thaana(self):
        self.assertTrue(contains_thaana("ދިވެހި"))
        self.assertFalse(contains_thaana("Dhivehi"))

    def test_detects_arabic_without_mistaking_thaana_for_arabic(self):
        self.assertTrue(contains_arabic_script("السلام عليكم"))
        self.assertFalse(contains_arabic_script("އައްސަލާމު ޢަލައިކުމް"))

    def test_detects_bengali_without_rejecting_thaana_or_latin_names(self):
        self.assertTrue(contains_foreign_script("އহ্যালো"))
        self.assertFalse(contains_foreign_script("ހެލޯ Musheer"))

    def test_recognizes_dhivehi_commands(self):
        self.assertTrue(is_dhivehi_trigger("dhivehi"))
        self.assertTrue(is_dhivehi_trigger("translate:dv"))
        self.assertTrue(is_dhivehi_trigger("translate:DIV"))
        self.assertFalse(is_dhivehi_trigger("translate:ar"))

    def test_normalizes_thaana_without_hidden_direction_marks(self):
        prepared = prepare_dhivehi_output("ހެލޯ\nރަނގަޅު")
        self.assertEqual(prepared, "ހެލޯ\nރަނގަޅު")
        self.assertEqual(prepare_dhivehi_output(prepared), prepared)

    def test_removes_existing_direction_controls(self):
        self.assertEqual(prepare_dhivehi_output("\u200fހެލޯ"), "ހެލޯ")

    def test_common_greetings_are_stable(self):
        self.assertEqual(common_dhivehi_translation("hey there"), "ހެލޯ")
        self.assertEqual(common_dhivehi_translation("HELLO!"), "ހެލޯ!")
        self.assertEqual(common_dhivehi_translation("hello?"), "ހެލޯ؟")
        self.assertIsNone(common_dhivehi_translation("Please review this document"))

    def test_leaves_non_thaana_text_unchanged(self):
        self.assertEqual(prepare_dhivehi_output("Hello\nworld"), "Hello\nworld")

    def test_rejects_translation_notes_leaked_by_model(self):
        contaminated = (
            "ހެލޯ -> Thaana: Let's use natural conversational Dhivehi for hey there. "
            "Wait, how about a friendly equivalent?"
        )
        self.assertFalse(is_clean_dhivehi_translation(contaminated, "hey there"))

    def test_accepts_clean_translation_and_preserved_name(self):
        self.assertTrue(is_clean_dhivehi_translation("ހެލޯ މުޝީރު.", "Hello Musheer"))
        self.assertTrue(
            is_clean_dhivehi_translation("މިއަދު Musheer އަންނާނެ.", "Musheer will come today")
        )

    def test_rejects_output_without_thaana(self):
        self.assertFalse(is_clean_dhivehi_translation("Hello there", "Hello there"))

    def test_rejects_mixed_arabic_and_thaana(self):
        self.assertFalse(
            is_clean_dhivehi_translation("السلام عليكم ރަނގަޅު", "salaam alaikum")
        )

    def test_rejects_mixed_bengali_and_thaana(self):
        self.assertFalse(is_clean_dhivehi_translation("އহ্যালো", "hey there"))


if __name__ == "__main__":
    unittest.main()
