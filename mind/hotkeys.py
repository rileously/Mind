from __future__ import annotations


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000


PALETTE_SHORTCUTS: dict[str, tuple[int, int]] = {
    "Ctrl+Alt+M": (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("M")),
    "Ctrl+Shift+M": (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("M")),
    "Ctrl+Alt+Space": (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x20),
    "Ctrl+Shift+Space": (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x20),
}

SNIP_SHORTCUTS: dict[str, tuple[int, int]] = {
    "Ctrl+Alt+S": (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("S")),
    "Ctrl+Shift+S": (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("S")),
    "Ctrl+Alt+X": (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("X")),
}

CLIPBOARD_HISTORY_SHORTCUTS: dict[str, tuple[int, int]] = {
    "Ctrl+Alt+V": (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("V")),
    "Ctrl+Shift+V": (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("V")),
    "Ctrl+Alt+H": (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("H")),
}


def shortcut_candidates(preferred: str) -> list[tuple[str, int, int]]:
    """Put the chosen shortcut first, followed by quiet automatic fallbacks."""
    selected = preferred if preferred in PALETTE_SHORTCUTS else next(iter(PALETTE_SHORTCUTS))
    ordered = [selected, *(name for name in PALETTE_SHORTCUTS if name != selected)]
    return [(name, *PALETTE_SHORTCUTS[name]) for name in ordered]


def snip_shortcut_candidates(preferred: str) -> list[tuple[str, int, int]]:
    selected = preferred if preferred in SNIP_SHORTCUTS else next(iter(SNIP_SHORTCUTS))
    ordered = [selected, *(name for name in SNIP_SHORTCUTS if name != selected)]
    return [(name, *SNIP_SHORTCUTS[name]) for name in ordered]


def clipboard_history_shortcut_candidates(preferred: str) -> list[tuple[str, int, int]]:
    selected = preferred if preferred in CLIPBOARD_HISTORY_SHORTCUTS else next(iter(CLIPBOARD_HISTORY_SHORTCUTS))
    ordered = [selected, *(name for name in CLIPBOARD_HISTORY_SHORTCUTS if name != selected)]
    return [(name, *CLIPBOARD_HISTORY_SHORTCUTS[name]) for name in ordered]



