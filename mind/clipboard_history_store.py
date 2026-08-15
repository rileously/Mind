from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


def detect_clipboard_category(text: str) -> str:
    """Classify text snippet into categories: link, json, code, number, or text."""
    stripped = text.strip()
    if not stripped:
        return "text"

    # URL / Link detection
    if re.match(r"^https?://[^\s]+$", stripped, re.IGNORECASE) or re.match(r"^www\.[^\s]+$", stripped, re.IGNORECASE):
        return "link"

    # Numeric / Currency
    if re.match(r"^[\$€£¥\w]{0,3}\s*[\d,]+(\.\d+)?\s*[\$€£¥\w]{0,3}$", stripped) and any(c.isdigit() for c in stripped):
        return "number"

    # JSON detection
    if (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
        try:
            json.loads(stripped)
            return "json"
        except Exception:
            pass

    # Code detection (keywords, syntax structures)
    code_indicators = [
        r"\b(def|class|import|from|return|function|const|let|var|if|else|for|while)\b",
        r"=>",
        r"</?[a-zA-Z0-9]+>",
        r"[{};]\s*$",
        r"console\.log",
        r"print\(",
    ]
    if any(re.search(pat, stripped) for pat in code_indicators) and ("\n" in stripped or ";" in stripped or "{" in stripped):
        return "code"

    return "text"


class ClipboardHistoryStore:
    def __init__(self, data_root: Path, max_unpinned: int = 100):
        self.data_root = Path(data_root)
        self.path = self.data_root / "clipboard_history.json"
        self.max_unpinned = max_unpinned
        self._entries: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._entries = []
            return
        try:
            with self.path.open("r", encoding="utf-8-sig") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self._entries = [item for item in data if isinstance(item, dict) and "text" in item]
                else:
                    self._entries = []
        except (OSError, ValueError, json.JSONDecodeError):
            self._entries = []

    def _save(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".clipboard_history.", suffix=".tmp", dir=self.data_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self._entries, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def add_entry(self, text: str) -> dict[str, Any] | None:
        cleaned = text.strip()
        if not cleaned:
            return None

        # Check if identical to the most recent entry
        if self._entries and self._entries[0].get("text") == text:
            # Refresh timestamp
            self._entries[0]["timestamp"] = time.time()
            self._save()
            return self._entries[0]

        # If it exists elsewhere in history and isn't pinned, remove it so it moves to top
        for idx, item in enumerate(self._entries):
            if item.get("text") == text:
                if not item.get("pinned", False):
                    self._entries.pop(idx)
                else:
                    # If pinned, just update timestamp
                    item["timestamp"] = time.time()
                    self._save()
                    return item
                break

        entry = {
            "id": str(uuid.uuid4()),
            "text": text,
            "timestamp": time.time(),
            "pinned": False,
            "char_count": len(text),
            "word_count": len(cleaned.split()),
            "category": detect_clipboard_category(text),
        }
        self._entries.insert(0, entry)

        # Enforce max unpinned limit
        unpinned = [e for e in self._entries if not e.get("pinned", False)]
        if len(unpinned) > self.max_unpinned:
            # Find excess unpinned from the end
            excess = len(unpinned) - self.max_unpinned
            removed = 0
            for i in range(len(self._entries) - 1, -1, -1):
                if not self._entries[i].get("pinned", False):
                    self._entries.pop(i)
                    removed += 1
                    if removed >= excess:
                        break

        self._save()
        return entry

    def get_entries(self, query: str = "", category: str = "all") -> list[dict[str, Any]]:
        results = []
        terms = query.strip().lower()
        cat = category.strip().lower()

        # Sort: pinned first, then by timestamp descending
        sorted_entries = sorted(
            self._entries,
            key=lambda e: (not e.get("pinned", False), -e.get("timestamp", 0)),
        )

        for item in sorted_entries:
            # Filter by category
            if cat == "pinned" and not item.get("pinned", False):
                continue
            elif cat != "all" and cat != "pinned" and item.get("category", "").lower() != cat:
                continue

            # Filter by query
            if terms and terms not in item.get("text", "").lower():
                continue

            results.append(item)

        return results

    def toggle_pin(self, entry_id: str) -> bool:
        for item in self._entries:
            if item.get("id") == entry_id:
                item["pinned"] = not item.get("pinned", False)
                self._save()
                return item["pinned"]
        return False

    def delete_entry(self, entry_id: str) -> bool:
        for idx, item in enumerate(self._entries):
            if item.get("id") == entry_id:
                self._entries.pop(idx)
                self._save()
                return True
        return False

    def clear_unpinned(self) -> int:
        initial = len(self._entries)
        self._entries = [e for e in self._entries if e.get("pinned", False)]
        removed = initial - len(self._entries)
        if removed > 0:
            self._save()
        return removed
