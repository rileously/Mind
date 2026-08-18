"""Letting a second launch reach the copy of Mind already running.

Closing Mind hides its window rather than quitting, and Windows keeps the
window's handle for as long as the process lives. A second launch could
therefore find that handle and call ShowWindow on it - which is what left a
blank white window on screen. The OS made the frame visible while Qt still
believed the widget was hidden, so the sidebar and the pages inside it were
never laid out and never painted. A correct title bar around nothing at all.

So the second launch asks instead of poking: it posts a message that only Mind
registers, and the running copy shows its own window through Qt, which is the
only thing that knows how. Hidden windows still receive posted messages - it is
how the Palette hotkey already works while Mind sits in the tray.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


# The title to look for. Kept here so the launcher and the window agree.
WINDOW_TITLES = ("Mind • AI Writing Workspace", "Mind")
# Registering the same string in any process returns the same number, which is
# what makes this work across two copies of Mind without a shared channel.
SHOW_MESSAGE_NAME = "MindDesktop.ShowWindow"
# A button on a notification arrives as a whole new process. It leaves what it
# wants in a file and posts this, and the copy that owns the phone reads it.
ACTION_MESSAGE_NAME = "MindDesktop.Action"

_user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
_show_message: int = 0


def show_message_id() -> int:
    """The message number Mind uses to mean "show your window"."""
    global _show_message
    if _show_message or _user32 is None:
        return _show_message
    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _show_message = int(_user32.RegisterWindowMessageW(SHOW_MESSAGE_NAME) or 0)
    return _show_message


_action_message: int = 0


def action_message_id() -> int:
    """The message number Mind uses to mean "something is waiting for you"."""
    global _action_message
    if _action_message or _user32 is None:
        return _action_message
    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _action_message = int(_user32.RegisterWindowMessageW(ACTION_MESSAGE_NAME) or 0)
    return _action_message


def tell_running_instance(message: int) -> bool:
    """Post one of Mind's own messages to the copy already running."""
    if _user32 is None:
        return False
    handle = find_window()
    if not handle or not message:
        return False
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId(wintypes.HWND(handle), ctypes.byref(pid))
    if pid.value:
        _user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        _user32.AllowSetForegroundWindow(pid)
    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    return bool(_user32.PostMessageW(wintypes.HWND(handle), message, 0, 0))


def find_window() -> int:
    """The handle of the running copy's main window, or 0 if there is none."""
    if _user32 is None:
        return 0
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    for title in WINDOW_TITLES:
        handle = _user32.FindWindowW(None, title)
        if handle:
            return int(handle)
    return 0


def ask_running_instance_to_show() -> bool:
    """Ask the copy already running to show its window. True if one was asked.

    Nothing here shows anything itself. Windows also refuses to let a process
    that has no window of its own steal the foreground, so permission to come
    forward is handed over first - otherwise Mind would reappear behind
    whatever the user was looking at.
    """
    if _user32 is None:
        return False
    handle = find_window()
    message = show_message_id()
    if not handle or not message:
        return False

    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId(wintypes.HWND(handle), ctypes.byref(pid))
    if pid.value:
        _user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        _user32.AllowSetForegroundWindow(pid)

    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    return bool(_user32.PostMessageW(wintypes.HWND(handle), message, 0, 0))
