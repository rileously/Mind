import unittest
from unittest.mock import patch

from mind.transform_client import TransformError, _clean_result, _read_gemini, transform_text


class TransformClientTests(unittest.TestCase):
    def test_clean_result_removes_markdown_fence(self):
        self.assertEqual("Improved text.", _clean_result("```text\nImproved text.\n```"))

    def test_clean_result_rejects_empty_text(self):
        with self.assertRaises(TransformError):
            _clean_result("   ")

    def test_gemini_reader_ignores_thinking_parts(self):
        response = {
            "candidates": [{
                "content": {
                    "parts": [
                        {"thought": True, "text": "Let's reason about the translation."},
                        {"text": "ހެލޯ"},
                    ]
                }
            }]
        }
        with patch("mind.transform_client._read_json", return_value=response):
            self.assertEqual(_read_gemini(object()), "ހެލޯ")

    def test_temperature_override_is_used_for_strict_retry(self):
        config = {"provider": "gemini", "model": "test-model", "temperature": 0.8}
        with patch("mind.transform_client._gemini", return_value="ހެލޯ") as gemini:
            transform_text(config, ["key"], "hello", "translate", temperature_override=0.0)
        self.assertEqual(gemini.call_args.args[-1], 0.0)

    def test_model_override_routes_specialized_translation(self):
        config = {"provider": "gemini", "model": "lite-model", "temperature": 0.5}
        with patch("mind.transform_client._gemini", return_value="ހެލޯ") as gemini:
            transform_text(config, ["key"], "hello", "translate", model_override="strong-model")
        self.assertEqual(gemini.call_args.args[0], "strong-model")

    def test_cloud_provider_requires_key(self):
        with self.assertRaisesRegex(TransformError, "No API key"):
            transform_text(
                {"provider": "gemini", "model": "example", "temperature": 0.5},
                [],
                "text",
                "Fix it.",
            )

    def test_missing_model_is_rejected_before_network(self):
        with self.assertRaisesRegex(TransformError, "No model"):
            transform_text(
                {"provider": "custom", "model": "", "endpoint": "http://localhost:1/v1"},
                [],
                "text",
                "Fix it.",
            )


if __name__ == "__main__":
    unittest.main()
