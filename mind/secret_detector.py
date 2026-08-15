from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SecretFinding:
    secret_type: str
    severity: str  # "high" | "medium"
    matched_text: str
    start: int
    end: int
    masked_text: str


def luhn_checksum_valid(number_str: str) -> bool:
    digits = [int(c) for c in number_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, digit in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = digit * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += digit
    return checksum % 10 == 0


def mask_secret_value(value: str, secret_type: str) -> str:
    cleaned = value.strip()
    length = len(cleaned)
    if "private key" in secret_type.lower():
        lines = cleaned.splitlines()
        header = lines[0] if lines else "-----BEGIN PRIVATE KEY-----"
        footer = lines[-1] if len(lines) > 1 else "-----END PRIVATE KEY-----"
        return f"{header}\n  •••••••• [ENCRYPTED PRIVATE KEY REDACTED] ••••••••\n{footer}"

    if length <= 8:
        return "••••••••"

    prefix_len = min(6, length // 4)
    suffix_len = min(4, length // 4)
    prefix = cleaned[:prefix_len]
    suffix = cleaned[-suffix_len:]
    bullets = "•" * min(16, length - prefix_len - suffix_len)
    return f"{prefix}{bullets}{suffix}"


SECRET_PATTERNS: list[tuple[str, str, str, re.Pattern]] = [
    (
        "OpenAI API Key",
        "high",
        r"\b(sk-(?!ant-)(?:proj-|admin-)?[a-zA-Z0-9_-]{32,})\b",
        re.compile(r"\b(sk-(?!ant-)(?:proj-|admin-)?[a-zA-Z0-9_-]{32,})\b"),
    ),
    (
        "Anthropic API Key",
        "high",
        r"\b(sk-ant-[a-zA-Z0-9_-]{32,})\b",
        re.compile(r"\b(sk-ant-[a-zA-Z0-9_-]{32,})\b"),
    ),
    (
        "Google AI / Firebase API Key",
        "high",
        r"\b(AIza[0-9A-Za-z-_]{30,40})\b",
        re.compile(r"\b(AIza[0-9A-Za-z-_]{30,40})\b"),
    ),
    (
        "GitHub Personal Token",
        "high",
        r"\b((?:gh[pousr]_[0-9a-zA-Z]{36}|github_pat_[0-9a-zA-Z_]{82}))\b",
        re.compile(r"\b((?:gh[pousr]_[0-9a-zA-Z]{36}|github_pat_[0-9a-zA-Z_]{82}))\b"),
    ),
    (
        "AWS Access Key ID",
        "high",
        r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b",
        re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"),
    ),
    (
        "Stripe Secret Key",
        "high",
        r"\b((?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{24,})\b",
        re.compile(r"\b((?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{24,})\b"),
    ),
    (
        "Slack Token",
        "high",
        r"\b(xox[baprs]-[0-9a-zA-Z-]{24,})\b",
        re.compile(r"\b(xox[baprs]-[0-9a-zA-Z-]{24,})\b"),
    ),
    (
        "Private Key",
        "high",
        r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----[^-]+-----END (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----",
        re.compile(
            r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----[^-]+-----END (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
]


def detect_secrets(text: str) -> list[SecretFinding]:
    """Inspect text for API tokens, private keys, and payment credentials."""
    if not text or len(text.strip()) < 8:
        return []

    findings: list[SecretFinding] = []

    # Check known secret regex patterns
    for type_name, severity, _pat_str, compiled in SECRET_PATTERNS:
        for match in compiled.finditer(text):
            matched = match.group(0)
            findings.append(
                SecretFinding(
                    secret_type=type_name,
                    severity=severity,
                    matched_text=matched,
                    start=match.start(),
                    end=match.end(),
                    masked_text=mask_secret_value(matched, type_name),
                )
            )

    # Check Credit Card numbers with Luhn validation
    # Matches patterns like 4532 1234 5678 9010 or 4532-1234-5678-9010
    card_pattern = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
    for match in card_pattern.finditer(text):
        candidate = match.group(0)
        digits_only = "".join(c for c in candidate if c.isdigit())
        if 13 <= len(digits_only) <= 19 and luhn_checksum_valid(digits_only):
            # Check card prefix (Visa=4, Mastercard=51-55 or 22-27, Amex=34/37, Discover=6011/65)
            if digits_only[0] in "3456":
                findings.append(
                    SecretFinding(
                        secret_type="Payment Card Number",
                        severity="high",
                        matched_text=candidate,
                        start=match.start(),
                        end=match.end(),
                        masked_text=f"••••-••••-••••-{digits_only[-4:]}",
                    )
                )

    # Sort findings by start position
    findings.sort(key=lambda f: f.start)
    return findings


def redact_all_secrets(text: str) -> str:
    """Replace all detected secrets in text with safe masked placeholders."""
    findings = detect_secrets(text)
    if not findings:
        return text

    result = []
    last_idx = 0
    for f in findings:
        if f.start < last_idx:
            continue
        result.append(text[last_idx : f.start])
        result.append(f.masked_text)
        last_idx = f.end
    result.append(text[last_idx:])
    return "".join(result)
