import unittest

from mind.converter_tools import (
    convert_currency,
    convert_timezone,
    convert_unit,
    detect_and_convert,
)


class ConverterToolsTests(unittest.TestCase):
    # 1. Currency Tests
    def test_usd_currency_conversion(self):
        res = convert_currency("$49.99")
        self.assertIsNotNone(res)
        self.assertEqual(res.category, "currency")
        self.assertIn("MVR", res.output_text)
        self.assertIn("EUR", res.output_text)

    def test_mvr_currency_conversion(self):
        res = convert_currency("500 MVR")
        self.assertIsNotNone(res)
        self.assertIn("USD", res.output_text)

    def test_mvr_symbol_conversion(self):
        res = convert_currency("500rf")
        self.assertIsNotNone(res)
        self.assertIn("USD", res.output_text)

    def test_eur_currency_conversion(self):
        res = convert_currency("€100")
        self.assertIsNotNone(res)
        self.assertIn("USD", res.output_text)
        self.assertIn("MVR", res.output_text)

    def test_gbp_currency_conversion(self):
        res = convert_currency("50 GBP")
        self.assertIsNotNone(res)
        self.assertIn("USD", res.output_text)

    def test_inr_currency_conversion(self):
        res = convert_currency("₹1500")
        self.assertIsNotNone(res)
        self.assertIn("USD", res.output_text)

    # 2. Temperature Tests
    def test_fahrenheit_conversion(self):
        res = convert_unit("72°F")
        self.assertIsNotNone(res)
        self.assertEqual(res.category, "unit")
        self.assertIn("22.2°C", res.output_text)

    def test_celsius_conversion(self):
        res = convert_unit("25 C")
        self.assertIsNotNone(res)
        self.assertIn("77.0°F", res.output_text)

    def test_kelvin_conversion(self):
        res = convert_unit("300K")
        self.assertIsNotNone(res)
        self.assertIn("26.9°C", res.output_text)

    # 3. Height & Length Tests
    def test_feet_inches_height(self):
        res = convert_unit("6'2\"")
        self.assertIsNotNone(res)
        self.assertIn("188.0 cm", res.output_text)
        self.assertIn("1.88 m", res.output_text)

    def test_miles_to_km(self):
        res = convert_unit("5 miles")
        self.assertIsNotNone(res)
        self.assertIn("8.05 km", res.output_text)

    def test_km_to_miles(self):
        res = convert_unit("10 km")
        self.assertIsNotNone(res)
        self.assertIn("6.21 miles", res.output_text)

    def test_inches_to_cm(self):
        res = convert_unit("12 in")
        self.assertIsNotNone(res)
        self.assertIn("30.48 cm", res.output_text)

    # 4. Weight & Mass Tests
    def test_lbs_to_kg(self):
        res = convert_unit("150 lbs")
        self.assertIsNotNone(res)
        self.assertIn("68.04 kg", res.output_text)

    def test_kg_to_lbs(self):
        res = convert_unit("70 kg")
        self.assertIsNotNone(res)
        self.assertIn("154.32 lbs", res.output_text)

    def test_oz_to_g(self):
        res = convert_unit("16 oz")
        self.assertIsNotNone(res)
        self.assertIn("453.6 g", res.output_text)

    # 5. Speed Tests
    def test_mph_to_kmh(self):
        res = convert_unit("60 mph")
        self.assertIsNotNone(res)
        self.assertIn("96.56 km/h", res.output_text)

    def test_kmh_to_mph(self):
        res = convert_unit("100 km/h")
        self.assertIsNotNone(res)
        self.assertIn("62.14 mph", res.output_text)

    # 6. Volume Tests
    def test_gal_to_liters(self):
        res = convert_unit("1 gal")
        self.assertIsNotNone(res)
        self.assertIn("3.79 L", res.output_text)

    def test_liters_to_gal(self):
        res = convert_unit("2 L")
        self.assertIsNotNone(res)
        self.assertIn("0.53 gal", res.output_text)

    # 7. Area Tests
    def test_sqft_to_sqm(self):
        res = convert_unit("1000 sq ft")
        self.assertIsNotNone(res)
        self.assertIn("92.90 m²", res.output_text)

    def test_acres_to_ha(self):
        res = convert_unit("5 acres")
        self.assertIsNotNone(res)
        self.assertIn("2.02 ha", res.output_text)

    # 8. Digital Storage Tests
    def test_mb_to_gb(self):
        res = convert_unit("2048 MB")
        self.assertIsNotNone(res)
        self.assertIn("2.00 GB", res.output_text)

    def test_mbps_speed(self):
        res = convert_unit("100 Mbps")
        self.assertIsNotNone(res)
        self.assertIn("12.50 MB/s", res.output_text)

    # 9. Timezone Tests
    def test_est_timezone_conversion(self):
        res = convert_timezone("10:00 PM EST")
        self.assertIsNotNone(res)
        self.assertEqual(res.category, "timezone")
        self.assertIn("MVT", res.output_text)
        self.assertIn("UTC", res.output_text)

    def test_pst_timezone_conversion(self):
        res = convert_timezone("9am PST")
        self.assertIsNotNone(res)
        self.assertIn("MVT", res.output_text)

    def test_utc_timezone_conversion(self):
        res = convert_timezone("3:30 PM UTC")
        self.assertIsNotNone(res)
        self.assertIn("8:30 PM", res.output_text)
        self.assertIn("MVT", res.output_text)

    # 10. Unified detect_and_convert
    def test_detect_and_convert_unified(self):
        self.assertIsNotNone(detect_and_convert("$19.99"))
        self.assertIsNotNone(detect_and_convert("72°F"))
        self.assertIsNotNone(detect_and_convert("10:00 PM EST"))
        self.assertIsNone(detect_and_convert("Hello world"))
        self.assertIsNone(detect_and_convert("123"))


if __name__ == "__main__":
    unittest.main()
