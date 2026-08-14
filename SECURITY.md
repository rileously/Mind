# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Mind, please use the repository's
[private vulnerability reporting form](https://github.com/rileously/Mind/security/advisories/new).
Do not open a public issue or include API keys, credentials, or other secrets in a report.

Please include:
- Description of the vulnerability
- Steps to reproduce
- Affected component (e.g., keystroke handling, clipboard operations, API calls)
- Potential impact

The Mind maintainer will review the report and coordinate a fix and disclosure timeline with you.

## Scope

Security issues I'm particularly interested in:

| Area | Examples |
|:-----|:--------|
| **Keystroke handling** | Buffer leakage, unintended capture outside trigger detection |
| **Clipboard operations** | Data exposure, failing to exclude from clipboard history |
| **API communication** | Credential exposure in logs or error messages, insecure transmission |
| **Configuration** | API keys stored insecurely, path traversal in config loading |
| **Shell commands** | Command injection via replacer-shell commands |

## Out of Scope

- Vulnerabilities in upstream AI providers (Gemini, Groq, etc.)
- Issues requiring physical access to an unlocked machine
- Social engineering attacks
- Denial of service against the app itself

## Disclosure

Please do **not** open a public GitHub issue for security vulnerabilities. I'll coordinate disclosure with you once a fix is available.
