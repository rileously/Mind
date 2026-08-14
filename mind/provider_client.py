from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class ProviderError(RuntimeError):
    pass


def test_provider(profile: str, model: str, endpoint: str, keys: list[str]) -> str:
    """Perform a read-only provider connection check."""
    profile = profile.lower().strip()
    if profile == "gemini":
        if not keys:
            raise ProviderError("Add a Gemini API key before testing.")
        url = "https://generativelanguage.googleapis.com/v1beta/models?key=" + urllib.parse.quote(keys[0])
        payload = _request_json(url, headers={})
        models = payload.get("models", []) if isinstance(payload, dict) else []
        available = {str(item.get("name", "")).split("/")[-1] for item in models if isinstance(item, dict)}
        if model and available and model not in available:
            return f"Connected to Gemini. The saved model was not listed; choose another model if requests fail."
        return "Connected to Gemini successfully."

    if profile == "groq":
        if not keys:
            raise ProviderError("Add a Groq API key before testing.")
        payload = _request_json(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {keys[0]}"},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderError("Groq returned an unexpected response.")
        return "Connected to Groq successfully."

    base = endpoint.rstrip("/")
    if not base:
        raise ProviderError("Enter an OpenAI-compatible endpoint first.")
    headers = {"Authorization": f"Bearer {keys[0]}"} if keys else {}
    payload = _request_json(f"{base}/models", headers=headers)
    if not isinstance(payload, dict):
        raise ProviderError("The endpoint returned an unexpected response.")
    return "Connected to the local/custom provider successfully."


def _request_json(url: str, headers: dict[str, str]) -> object:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Mind/0.1", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ProviderError("The provider rejected the API key.") from exc
        if exc.code == 404:
            raise ProviderError("The endpoint does not expose a compatible /models route.") from exc
        raise ProviderError(f"The provider returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ProviderError(f"Could not connect: {reason}") from exc
    except TimeoutError as exc:
        raise ProviderError("The connection timed out.") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("The provider did not return valid JSON.") from exc

