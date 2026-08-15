import unittest
from decimal import Decimal

from mind.math_tools import (
    MathInputError,
    extract_numbers,
    is_math_or_number_problem,
    normalize_math_text,
    solve_math_locally,
    sum_number_list,
)


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

    def test_solve_math_locally(self):
        self.assertEqual(solve_math_locally("2 + 2"), "4")
        self.assertEqual(solve_math_locally("(15 * 4) + 10 / 2"), "65")
        self.assertEqual(solve_math_locally("15% of 80"), "12")
        self.assertEqual(solve_math_locally("2^8"), "256")
        self.assertIsNone(solve_math_locally("2x + 5 = 15"))
        self.assertIsNone(solve_math_locally("x^2 - 4 = 0"))
        self.assertIsNone(solve_math_locally("Just text"))

    def test_is_math_or_number_problem(self):
        self.assertTrue(is_math_or_number_problem("2x + 5 = 15"))
        self.assertTrue(is_math_or_number_problem("x^2 - 5x + 6 = 0"))
        self.assertTrue(is_math_or_number_problem("12 * (4 + 5)"))
        self.assertTrue(is_math_or_number_problem("calculate 25% of 300"))
        self.assertTrue(is_math_or_number_problem("find the value of x when 3x = 9"))
        self.assertTrue(is_math_or_number_problem("\\frac{a}{b} = 2"))
        self.assertTrue(is_math_or_number_problem('"If a car travels 60 miles\\nin 2 hours, what is the speed"'))
        self.assertTrue(is_math_or_number_problem("If a car travels 60 miles in 2 hours, what is the speed"))
        self.assertTrue(is_math_or_number_problem("If John has 5 apples and eats 2, how many apples are left?"))
        self.assertTrue(is_math_or_number_problem("A train moves at 50 mph for 3 hours, what is the total distance?"))
        self.assertFalse(is_math_or_number_problem("Hello world!"))
        self.assertFalse(is_math_or_number_problem("The quick brown fox."))
        self.assertFalse(is_math_or_number_problem("My phone number is 1234567890."))


if __name__ == "__main__":
    unittest.main()
