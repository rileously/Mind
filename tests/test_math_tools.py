import unittest
from decimal import Decimal

from mind.math_tools import MathInputError, extract_numbers, normalize_math_text, sum_number_list


class MathToolsTests(unittest.TestCase):
    def test_sums_integer_list(self):
        self.assertEqual(sum_number_list("100\n200\n50"), "100 + 200 + 50 = 350")

    def test_supports_thousands_decimals_and_accounting_negatives(self):
        result = sum_number_list("1,200.50\n300.25\n(50.75)\n-10")
        self.assertEqual(result, "1,200.5 + 300.25 − 50.75 − 10 = 1,440")

    def test_ignores_numbered_list_labels(self):
        self.assertEqual(extract_numbers("1. 250\n2) 300"), [Decimal("250"), Decimal("300")])

    def test_formats_long_lists_as_compact_total(self):
        text = "\n".join(str(value) for value in range(1, 14))
        self.assertEqual(sum_number_list(text), "Total (13 numbers): 91")

    def test_normalizes_common_ocr_math_symbols(self):
        self.assertEqual(normalize_math_text("8 × 4 ÷ 2 − 3 ＝ 13"), "8 * 4 / 2 - 3 = 13")

    def test_reports_image_without_numbers(self):
        with self.assertRaisesRegex(MathInputError, "No numbers"):
            sum_number_list("No numeric values here")


if __name__ == "__main__":
    unittest.main()
