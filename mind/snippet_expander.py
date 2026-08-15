from __future__ import annotations

import random
import re
import uuid
from datetime import datetime


def expand_snippet_template(template: str, clipboard_text: str = "") -> str:
    """Expand dynamic variables like {date}, {time}, {clipboard}, {uuid} in text snippets."""
    if not template or "{" not in template:
        return template

    now = datetime.now()

    def _replace(match: re.Match) -> str:
        tag = match.group(1).strip().lower()
        if tag in ("date", "today"):
            return now.strftime("%B %d, %Y")
        if tag in ("date:iso", "date:short", "date:ymd"):
            return now.strftime("%Y-%m-%d")
        if tag in ("date:dmy", "date:uk"):
            return now.strftime("%d/%m/%Y")
        if tag in ("date:mdy", "date:us"):
            return now.strftime("%m/%d/%Y")
        if tag in ("time", "now"):
            return now.strftime("%I:%M %p").lstrip("0")
        if tag in ("time:24", "time:24h"):
            return now.strftime("%H:%M")
        if tag in ("datetime", "timestamp"):
            return now.strftime("%B %d, %Y %I:%M %p")
        if tag in ("datetime:iso", "iso"):
            return now.isoformat()
        if tag in ("day", "weekday"):
            return now.strftime("%A")
        if tag in ("month",):
            return now.strftime("%B")
        if tag in ("year",):
            return str(now.year)
        if tag in ("clipboard", "clip"):
            return clipboard_text
        if tag in ("uuid", "guid"):
            return str(uuid.uuid4())
        if tag.startswith("random:"):
            try:
                parts = tag.split(":", 1)[1].split("-")
                low, high = int(parts[0]), int(parts[1])
                return str(random.randint(low, high))
            except Exception:
                pass
        return match.group(0)

    return re.sub(r"\{([^{}]+)\}", _replace, template)
