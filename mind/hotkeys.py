from __future__ import annotations


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000


# Ordered from the least intrusive mnemonic to broader fallbacks.
PALETTE_SHORTCUTS: dict[str, tuple[int, int]] = {
    "Ctrl+Alt+M": (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("M")),
    "Ctrl+Shift+M": (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("M")),
    "Ctrl+Alt+Space": (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x20),
    "Ctrl+Shift+Space": (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x20),
}


def shortcut_candidates(preferred: str) -> list[tuple[str, int, int]]:
    """Put the chosen shortcut first, followed by quiet automatic fallbacks."""
    selected = preferred if preferred in PALETTE_SHORTCUTS else next(iter(PALETTE_SHORTCUTS))
    ordered = [selected, *(name for name in PALETTE_SHORTCUTS if name != selected)]
    return [(name, *PALETTE_SHORTCUTS[name]) for name in ordered]

