"""Reading an identity card with a vision model instead of Windows OCR.

Windows OCR reads printing. It does well on a flat, clean, well-lit card and
badly on a worn one photographed on a table, where it loses a surname to a
character of Thaana stuck against it or returns a sideways card as nothing at
all. A vision model reads a picture, which is a different and much easier
problem: it copes with the angle, the glare, the holograms and the script it
is not being asked to transcribe.

The cost is that the photograph leaves this PC. That is a real change from
everything else Mind does with images, and it is why this is off until it is
turned on, and why the panel says which way a card was read.

The model is asked for JSON and given no room to be helpful: transcribe, or
return an empty string. A vision model asked to read a blurred number will
produce a plausible one, and a plausible national ID is worse than none - it
puts a real stranger on a ticket rather than failing.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .id_card import Card, card_name, card_number


TIMEOUT = 45

# Asked for exactly what the card carries, in the script a ticket needs, with
# refusal made the easy option rather than a failure.
PROMPT = (
    "This is a photograph of a Maldivian national identity card. It may be "
    "rotated, worn, or partly in shadow.\n\n"
    "Transcribe three fields and reply with nothing but JSON:\n"
    '{"name": "", "number": "", "born": ""}\n\n'
    "name: the full name exactly as printed in Latin script, every part of it, "
    "spelled character for character. Do not translate, expand, abbreviate or "
    "tidy it.\n"
    "number: the identity card number, one letter followed by six digits.\n"
    "born: the date of birth exactly as printed, dd/mm/yyyy.\n\n"
    "If any field is unreadable, or you are not certain of every character in "
    "it, return an empty string for that field. A wrong name or number puts a "
    "stranger on a ferry ticket, so guessing is worse than returning nothing. "
    "Ignore the Thaana script and the photograph of the cardholder."
)


class AiOcrError(RuntimeError):
    """Raised when the model could not be reached or would not answer."""


def data_uri(image: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"


def parse_reply(reply: str) -> Card:
    """A card out of whatever the model wrapped its JSON in.

    Models fence JSON in markdown, prefix it with "Here is", or return it with
    a trailing full stop, so the object is found rather than assumed.
    """
    text = (reply or "").strip()
    if not text:
        raise AiOcrError("The model returned nothing.")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise AiOcrError("The model did not answer with JSON.")
    try:
        found = json.loads(match.group(0))
    except ValueError as exc:
        raise AiOcrError("The model's JSON could not be read.") from exc
    if not isinstance(found, dict):
        raise AiOcrError("The model did not answer with a card.")

    # Validated the same way a typed correction is: no letter-for-digit
    # repairs. A model that says O meant O, and a number that is not a letter
    # and six digits is not a number this can use.
    name = card_name(str(found.get("name") or ""))
    number = card_number(str(found.get("number") or ""))
    born = str(found.get("born") or "").strip()
    if not re.fullmatch(r"[0-3]?[0-9]/[01]?[0-9]/(?:19|20)[0-9]{2}", born):
        born = ""
    return Card(name=name, number=number, born=born)


def read_card(image: bytes, config: dict, keys: list, mime: str = "image/jpeg") -> Card:
    """Ask the configured provider to read one card."""
    if not image:
        raise AiOcrError("There was no image to read.")
    provider = str(config.get("provider", "gemini"))
    model = str(config.get("model", "")).strip()
    endpoint = str(config.get("endpoint", "")).rstrip("/")
    candidates = list(keys or ([] if provider != "custom" else ["local"]))
    if not candidates:
        raise AiOcrError("No API key is configured, so the card cannot be read by AI.")
    if not model:
        raise AiOcrError("No model is configured.")

    last = "The provider could not read the card."
    for key in candidates:
        try:
            if provider == "gemini":
                return parse_reply(_gemini(model, key, image, mime))
            base = endpoint if provider == "custom" else "https://api.groq.com/openai/v1"
            if not base:
                raise AiOcrError("No provider endpoint is configured.")
            return parse_reply(_openai(base, model, key, image, mime))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                last = "The provider rejected the API key."
                continue
            if exc.code == 429:
                last = "The provider is rate limited. Try again shortly."
                continue
            if exc.code == 413:
                raise AiOcrError("That photo is too large for this model.") from exc
            if exc.code == 400:
                # Nearly always a model that cannot see. Said plainly, because
                # the setting is new and the model was chosen for writing.
                raise AiOcrError(
                    f"{model} would not accept an image. Choose a model that "
                    "can see pictures, or turn AI card reading off."
                ) from exc
            raise AiOcrError(f"The provider returned HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise AiOcrError(
                f"Could not reach the provider: {getattr(exc, 'reason', exc)}"
            ) from exc
        except TimeoutError as exc:
            raise AiOcrError("The provider timed out.") from exc
    raise AiOcrError(last)


def _gemini(model: str, key: str, image: bytes, mime: str) -> str:
    safe = urllib.parse.quote(model, safe="")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{safe}:generateContent"
    body = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(image).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        # Nothing creative is wanted here: the answer is printed on the card.
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key,
            "User-Agent": "Mind/0.1",
        },
        method="POST",
    )
    payload = _read(request)
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        return "".join(str(part.get("text", "")) for part in parts)
    except (KeyError, IndexError, TypeError) as exc:
        raise AiOcrError("The provider returned an unreadable response.") from exc


def _openai(base: str, model: str, key: str, image: bytes, mime: str) -> str:
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri(image, mime)}},
                ],
            }
        ],
        "temperature": 0,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Mind/0.1",
        },
        method="POST",
    )
    payload = _read(request)
    try:
        return str(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise AiOcrError("The provider returned an unreadable response.") from exc


def _read(request: urllib.request.Request):
    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
        return json.loads(answer.read().decode("utf-8", "replace"))
