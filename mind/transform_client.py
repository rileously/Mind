from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .provider_response import extract_gemini_text


SYSTEM_PROMPT = (
    "You are a pure text transformation function. Apply the requested transformation to "
    "the text inside <input> tags. Preserve facts, names, numbers, links, and the original "
    "meaning. Return only the transformed text with no explanation or markdown.\n\n"
    "Transformation: "
)


class TransformError(RuntimeError):
    pass


class SecureRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlparse(req.full_url)
        new = urllib.parse.urlparse(newurl)
        if new.scheme != old.scheme or new.netloc != old.netloc:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SECURE_OPENER = urllib.request.build_opener(SecureRedirectHandler())


def transform_text(
    config: dict,
    keys: list[str],
    text: str,
    prompt: str,
    temperature_override: float | None = None,
    model_override: str | None = None,
) -> str:
    provider = str(config.get("provider", "gemini"))
    model = str(model_override if model_override is not None else config.get("model", "")).strip()
    endpoint = str(config.get("endpoint", "")).rstrip("/")
    temperature = float(
        temperature_override if temperature_override is not None else config.get("temperature", 0.5)
    )
    temperature = max(0.0, min(2.0, temperature))
    candidates = keys or (["local"] if provider == "custom" else [])
    if not candidates:
        raise TransformError("No API key is configured.")
    if not model:
        raise TransformError("No model is configured.")

    last_error = "The provider could not complete the request."
    for key in candidates:
        try:
            if provider == "gemini":
                return _gemini(model, key, text, prompt, temperature)
            base = endpoint if provider == "custom" else "https://api.groq.com/openai/v1"
            if not base:
                raise TransformError("No provider endpoint is configured.")
            return _openai_compatible(base, model, key, text, prompt, temperature)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                last_error = "The provider rejected the API key."
                continue
            if exc.code == 429:
                last_error = "The provider is rate limited. Try again shortly."
                continue
            if exc.code == 413:
                raise TransformError("The selected text is too long for this model.") from exc
            raise TransformError(f"The provider returned HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise TransformError(f"Could not reach the provider: {getattr(exc, 'reason', exc)}") from exc
        except TimeoutError as exc:
            raise TransformError("The provider timed out.") from exc
    raise TransformError(last_error)


def _gemini(model: str, key: str, text: str, prompt: str, temperature: float) -> str:
    safe_model = urllib.parse.quote(model, safe="")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{safe_model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT + prompt}]},
        "contents": [{"parts": [{"text": f"<input>\n{text}\n</input>"}]}],
        "generationConfig": {"temperature": temperature},
    }
    thinking = {"gemini-3.5-flash-lite": "low", "gemini-3.6-flash": "minimal"}.get(model)
    if thinking:
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": thinking}
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key, "User-Agent": "Mind/0.1"},
        method="POST",
    )
    return _read_gemini(request)


def _openai_compatible(base: str, model: str, key: str, text: str, prompt: str, temperature: float) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT + prompt},
            {"role": "user", "content": f"<input>\n{text}\n</input>"},
        ],
        "temperature": temperature,
    }
    if model == "openai/gpt-oss-120b":
        body.update({"reasoning_effort": "medium", "include_reasoning": False})
    elif model == "qwen/qwen3.6-27b":
        body["reasoning_effort"] = "none"
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "Mind/0.1"},
        method="POST",
    )
    data = _read_json(request)
    try:
        result = str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise TransformError("The provider returned an unreadable response.") from exc
    return _clean_result(result)


def _read_gemini(request: urllib.request.Request) -> str:
    data = _read_json(request)
    result = extract_gemini_text(data)
    if not result:
        raise TransformError("Gemini returned an unreadable response.")
    return _clean_result(result)


def _read_json(request: urllib.request.Request) -> object:
    with SECURE_OPENER.open(request, timeout=45) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise TransformError("The provider response was unexpectedly large.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransformError("The provider returned invalid data.") from exc


def _clean_result(result: str) -> str:
    result = result.strip()
    if result.startswith("```"):
        lines = result.splitlines()
        if lines and lines[0].startswith("```"):
            lines.pop(0)
        if lines and lines[-1].strip().startswith("```"):
            lines.pop()
        result = "\n".join(lines).strip()
    if not result:
        raise TransformError("The model returned an empty response.")
    return result
