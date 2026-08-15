import unittest

from mind.secret_detector import (
    detect_secrets,
    luhn_checksum_valid,
    mask_secret_value,
    redact_all_secrets,
)


class SecretDetectorTests(unittest.TestCase):
    def test_luhn_algorithm(self):
        # Valid test card (Visa test number)
        self.assertTrue(luhn_checksum_valid("4532015112830366"))
        # Invalid number
        self.assertFalse(luhn_checksum_valid("4532015112830367"))
        # Too short
        self.assertFalse(luhn_checksum_valid("12345"))

    def test_detect_openai_key(self):
        sample = "export OPENAI_API_KEY=sk-proj-abc1234567890abcdef1234567890abcdef123456"
        findings = detect_secrets(sample)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].secret_type, "OpenAI API Key")
        self.assertTrue(findings[0].masked_text.startswith("sk-pro"))
        self.assertTrue("•" in findings[0].masked_text)

    def test_detect_anthropic_key(self):
        sample = "anthropic_key = 'sk-ant-api03-abcdef1234567890abcdef12345678901234567890'"
        findings = detect_secrets(sample)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].secret_type, "Anthropic API Key")

    def test_detect_google_key(self):
        sample = "apiKey: 'AIzaSyA1234567890abcdef1234567890abcde'"
        findings = detect_secrets(sample)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].secret_type, "Google AI / Firebase API Key")

    def test_detect_github_token(self):
        sample = "Authorization: Bearer ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        findings = detect_secrets(sample)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].secret_type, "GitHub Personal Token")

    def test_detect_aws_key(self):
        sample = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        findings = detect_secrets(sample)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].secret_type, "AWS Access Key ID")

    def test_detect_private_key(self):
        sample = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0Y1234567890abcdef...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        findings = detect_secrets(sample)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].secret_type, "Private Key")

    def test_redact_all_secrets(self):
        text = "Key: sk-proj-1234567890abcdef1234567890abcdef123456 and token: ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        redacted = redact_all_secrets(text)
        self.assertNotIn("sk-proj-1234567890abcdef1234567890abcdef123456", redacted)
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertIn("••••", redacted)


if __name__ == "__main__":
    unittest.main()
