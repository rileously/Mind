from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_MENU = 0x12
SELF_INPUT_TAG = 0x53534B


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    # All three members are required for the native INPUT structure's 64-bit size.
    # A keyboard-only union is too small, causing SendInput to reject every event.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


def _key(vk: int, flags: int = 0) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.union.ki.wVk = vk
    event.union.ki.dwFlags = flags
    event.union.ki.dwExtraInfo = SELF_INPUT_TAG
    return event


def _send_ctrl_key(vk: int) -> bool:
    events = (INPUT * 4)(
        _key(VK_CONTROL),
        _key(vk),
        _key(vk, KEYEVENTF_KEYUP),
        _key(VK_CONTROL, KEYEVENTF_KEYUP),
    )
    sent = ctypes.windll.user32.SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))
    return sent == 4


def _send_key(vk: int) -> bool:
    events = (INPUT * 2)(
        _key(vk),
        _key(vk, KEYEVENTF_KEYUP),
    )
    sent = ctypes.windll.user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(INPUT))
    return sent == 2


def _modifiers_released(timeout: float = 0.8) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_CONTROL, VK_MENU)):
            return True
        QApplication.processEvents()
        time.sleep(0.015)
    return False


def _pump_events(duration: float) -> None:
    """Keep servicing delayed Windows clipboard rendering during short waits."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def clone_mime_data(source: QMimeData | None) -> QMimeData | None:
    if source is None:
        return None
    copy = QMimeData()
    for format_name in source.formats():
        copy.setData(format_name, source.data(format_name))
    if source.hasUrls() and not copy.hasUrls():
        copy.setUrls([QUrl(url) for url in source.urls()])
    return copy


class SelectionSession:
    """Capture a selection and later replace it while preserving the clipboard."""

    kind = "text"

    def __init__(self, target_hwnd: int, text: str, original_clipboard: QMimeData | None):
        self.target_hwnd = target_hwnd
        self.text = text
        self.original_clipboard = original_clipboard

    @classmethod
    def capture(cls, target_hwnd: int, timeout: float = 1.0) -> "SelectionSession | None":
        if not target_hwnd or not _modifiers_released():
            return None
        clipboard = QApplication.clipboard()
        original = clone_mime_data(clipboard.mimeData())
        sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
        clipboard.clear()
        QApplication.processEvents()
        cleared_sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
        if not _send_ctrl_key(0x43):  # C
            _restore_clipboard(original)
            return None

        deadline = time.monotonic() + max(0.1, min(float(timeout), 1.0))
        changed = False
        while time.monotonic() < deadline:
            QApplication.processEvents()
            current = ctypes.windll.user32.GetClipboardSequenceNumber()
            if current not in {sequence, cleared_sequence} and clipboard.mimeData().hasText():
                changed = True
                break
            time.sleep(0.015)
        selected = clipboard.text() if changed else ""
        _restore_clipboard(original)
        if not selected.strip():
            return None
        return cls(target_hwnd, selected, original)

    def replace(self, result: str) -> bool:
        if not result:
            return False
        user32 = ctypes.windll.user32
        if not user32.IsWindow(self.target_hwnd):
            QApplication.clipboard().setText(result)
            return False
        user32.SetForegroundWindow(self.target_hwnd)
        _pump_events(0.12)
        if user32.GetForegroundWindow() != self.target_hwnd or not _modifiers_released(0.4):
            QApplication.clipboard().setText(result)
            return False

        clipboard = QApplication.clipboard()
        clipboard.setText(result)
        _pump_events(0.12)
        pasted = _send_ctrl_key(0x56)  # V
        # QClipboard can use delayed rendering on Windows. The target's Ctrl+V request
        # is serviced by Mind's event loop, so sleeping here can make the target receive
        # an empty clipboard and delete the selected text. Pump until paste is committed.
        _pump_events(0.55)
        if pasted:
            _restore_clipboard(self.original_clipboard)
            _pump_events(0.08)
        return pasted

    def delete_selected_text(self, virtual_key: int) -> bool:
        """Return focus to the source and forward Backspace or Delete safely."""
        if virtual_key not in {0x08, 0x2E}:  # VK_BACK, VK_DELETE
            return False
        user32 = ctypes.windll.user32
        if not user32.IsWindow(self.target_hwnd):
            return False
        user32.SetForegroundWindow(self.target_hwnd)
        _pump_events(0.08)
        if user32.GetForegroundWindow() != self.target_hwnd or not _modifiers_released(0.4):
            return False
        return _send_key(virtual_key)


class ClipboardImageSession:
    """Keep a copied image intact while OCR output is pasted at the current caret."""

    kind = "image"

    def __init__(self, target_hwnd: int, image: QImage, original_clipboard: QMimeData | None):
        self.target_hwnd = target_hwnd
        self.image = image.copy()
        self.original_clipboard = original_clipboard
        self.text = ""

    @classmethod
    def capture(cls, target_hwnd: int) -> "ClipboardImageSession | None":
        if not target_hwnd or not _modifiers_released():
            return None
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is None or not mime.hasImage():
            return None
        image = clipboard.image()
        if image.isNull():
            image_data = mime.imageData()
            image = image_data if isinstance(image_data, QImage) else QImage()
        if image.isNull():
            return None
        return cls(target_hwnd, image, clone_mime_data(mime))

    def replace(self, result: str) -> bool:
        if not result:
            return False
        user32 = ctypes.windll.user32
        if not user32.IsWindow(self.target_hwnd):
            QApplication.clipboard().setText(result)
            return False
        user32.SetForegroundWindow(self.target_hwnd)
        _pump_events(0.12)
        if user32.GetForegroundWindow() != self.target_hwnd or not _modifiers_released(0.4):
            QApplication.clipboard().setText(result)
            return False

        clipboard = QApplication.clipboard()
        clipboard.setText(result)
        _pump_events(0.12)
        pasted = _send_ctrl_key(0x56)  # V
        _pump_events(0.55)
        if pasted:
            _restore_clipboard(self.original_clipboard)
            _pump_events(0.08)
        return pasted


def _restore_clipboard(original: QMimeData | None) -> None:
    clipboard = QApplication.clipboard()
    if original is None or not original.formats():
        clipboard.clear()
    else:
        clipboard.setMimeData(clone_mime_data(original))
    QApplication.processEvents()


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _VARIANT_VALUE(ctypes.Union):
    _fields_ = [
        ("lVal", ctypes.c_long),
        ("pdispVal", ctypes.c_void_p),
        ("punkVal", ctypes.c_void_p),
        ("llVal", ctypes.c_longlong),
    ]


class _VARIANT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
        ("wReserved2", wintypes.WORD),
        ("wReserved3", wintypes.WORD),
        ("value", _VARIANT_VALUE),
    ]


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


_EDIT_WINDOW_CLASSES = frozenset([
    "edit",
    "richedit",
    "richedit20w",
    "richedit20a",
    "richedit50w",
    "richedit60w",
    "scintilla",
    "textbox",
    "windows.ui.core.corewindow",
    "consolewindowclass",
])

_ROLE_SYSTEM_TEXT = 42
_ROLE_SYSTEM_STATICTEXT = 41
_ROLE_SYSTEM_DOCUMENT = 15
_ROLE_SYSTEM_COMBOBOX = 46
_STATE_SYSTEM_READONLY = 0x40
_UIA_EDIT_CONTROL_TYPE = 50004
_UIA_DOCUMENT_CONTROL_TYPE = 50030
_UIA_COMBOBOX_CONTROL_TYPE = 50003
_UIA_TEXT_CONTROL_TYPE = 50020
_UIA_BUTTON_CONTROL_TYPE = 50000
_UIA_VALUE_PATTERN_ID = 10002
_UIA_TEXT_EDIT_PATTERN_ID = 10032


def _vfunc(obj_ptr: int, index: int, argtypes: list, restype=ctypes.c_long):
    vtable_ptr = ctypes.cast(obj_ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable_ptr[index])


def is_editable_input_target(target_hwnd: int) -> bool:
    """Return True if target window or focused element is an editable text input field.

    Standard text selection (webpage paragraphs, static labels, read-only text) returns False.
    """
    if not target_hwnd:
        return False

    # In-process Qt widget fast check
    try:
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit, QWidget

        qt_widget = QWidget.find(target_hwnd)
        if qt_widget is not None:
            focus = qt_widget.focusWidget() or qt_widget
            if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit)):
                return not focus.isReadOnly()
            return False
    except Exception:
        pass

    user32 = ctypes.windll.user32
    if not user32.IsWindow(target_hwnd):
        return False

    focused_hwnd = target_hwnd

    # 1. Win32 Caret and Edit control class inspection
    try:
        tid = user32.GetWindowThreadProcessId(target_hwnd, None)
        gui_info = _GUITHREADINFO()
        gui_info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if user32.GetGUIThreadInfo(tid, ctypes.byref(gui_info)):
            if gui_info.hwndCaret:
                return True
            if gui_info.hwndFocus:
                focused_hwnd = gui_info.hwndFocus

        for check_hwnd in (focused_hwnd, target_hwnd):
            if not check_hwnd:
                continue
            buf = ctypes.create_unicode_buffer(256)
            if user32.GetClassNameW(check_hwnd, buf, 256):
                cls_name = buf.value.lower()
                if any(cls_name == ec or cls_name.startswith("richedit") for ec in _EDIT_WINDOW_CLASSES):
                    GWL_STYLE = -16
                    ES_READONLY = 0x0800
                    style = user32.GetWindowLongW(check_hwnd, GWL_STYLE)
                    if not (style & ES_READONLY):
                        return True
    except Exception:
        pass

    # 2. UI Automation inspection
    try:
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        clsid_uia = _GUID()
        iid_uia = _GUID()
        ole32.IIDFromString("{FF48DBA4-60EF-4201-AA87-54103EEF594E}", ctypes.byref(clsid_uia))
        ole32.IIDFromString("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}", ctypes.byref(iid_uia))
        p_uia = ctypes.c_void_p()
        if ole32.CoCreateInstance(ctypes.byref(clsid_uia), None, 1, ctypes.byref(iid_uia), ctypes.byref(p_uia)) == 0 and p_uia.value:
            try:
                GetFocusedElement = _vfunc(p_uia.value, 8, [ctypes.POINTER(ctypes.c_void_p)])
                p_elem = ctypes.c_void_p()
                if GetFocusedElement(p_uia.value, ctypes.byref(p_elem)) == 0 and p_elem.value:
                    try:
                        get_ControlType = _vfunc(p_elem.value, 21, [ctypes.POINTER(ctypes.c_int)])
                        ct = ctypes.c_int()
                        if get_ControlType(p_elem.value, ctypes.byref(ct)) == 0:
                            if ct.value == _UIA_EDIT_CONTROL_TYPE:
                                GetCurrentPattern = _vfunc(p_elem.value, 12, [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])
                                p_pat = ctypes.c_void_p()
                                if GetCurrentPattern(p_elem.value, _UIA_VALUE_PATTERN_ID, ctypes.byref(p_pat)) == 0 and p_pat.value:
                                    try:
                                        get_ReadOnly = _vfunc(p_pat.value, 4, [ctypes.POINTER(ctypes.c_int)])
                                        ro = ctypes.c_int()
                                        if get_ReadOnly(p_pat.value, ctypes.byref(ro)) == 0 and ro.value:
                                            return False
                                    finally:
                                        _vfunc(p_pat.value, 2, [], restype=wintypes.ULONG)(p_pat.value)
                                return True
                            elif ct.value == _UIA_DOCUMENT_CONTROL_TYPE:
                                GetCurrentPattern = _vfunc(p_elem.value, 12, [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])
                                p_pat = ctypes.c_void_p()
                                if GetCurrentPattern(p_elem.value, _UIA_VALUE_PATTERN_ID, ctypes.byref(p_pat)) == 0 and p_pat.value:
                                    try:
                                        get_ReadOnly = _vfunc(p_pat.value, 4, [ctypes.POINTER(ctypes.c_int)])
                                        ro = ctypes.c_int()
                                        if get_ReadOnly(p_pat.value, ctypes.byref(ro)) == 0 and not ro.value:
                                            return True
                                    finally:
                                        _vfunc(p_pat.value, 2, [], restype=wintypes.ULONG)(p_pat.value)
                                p_tedit = ctypes.c_void_p()
                                if GetCurrentPattern(p_elem.value, _UIA_TEXT_EDIT_PATTERN_ID, ctypes.byref(p_tedit)) == 0 and p_tedit.value:
                                    _vfunc(p_tedit.value, 2, [], restype=wintypes.ULONG)(p_tedit.value)
                                    return True
                                return False
                            elif ct.value in (_UIA_TEXT_CONTROL_TYPE, _UIA_BUTTON_CONTROL_TYPE):
                                return False
                    finally:
                        _vfunc(p_elem.value, 2, [], restype=wintypes.ULONG)(p_elem.value)
            finally:
                _vfunc(p_uia.value, 2, [], restype=wintypes.ULONG)(p_uia.value)
    except Exception:
        pass

    # 3. MSAA (IAccessible) fallback
    try:
        oleacc = ctypes.windll.oleacc
        ole32 = ctypes.windll.ole32
        iid_iacc = _GUID()
        ole32.IIDFromString("{618736E0-3C3D-11CF-810C-00AA00389B71}", ctypes.byref(iid_iacc))
        for check_hwnd in (focused_hwnd, target_hwnd):
            if not check_hwnd:
                continue
            p_acc = ctypes.c_void_p()
            if oleacc.AccessibleObjectFromWindow(check_hwnd, 0xFFFFFFFC, ctypes.byref(iid_iacc), ctypes.byref(p_acc)) == 0 and p_acc.value:
                try:
                    get_accFocus = _vfunc(p_acc.value, 18, [ctypes.POINTER(_VARIANT)])
                    var_focus = _VARIANT()
                    hr_focus = get_accFocus(p_acc.value, ctypes.byref(var_focus))
                    target_iacc = p_acc.value
                    child_var = _VARIANT()
                    child_var.vt = 3  # VT_I4
                    child_var.pdispVal = 0  # CHILDID_SELF
                    release_disp = False
                    if hr_focus == 0:
                        if var_focus.vt == 9 and var_focus.pdispVal:  # VT_DISPATCH
                            target_iacc = var_focus.pdispVal
                            release_disp = True
                        elif var_focus.vt == 3 and var_focus.pdispVal:  # VT_I4
                            child_var = var_focus

                    get_accRole = _vfunc(target_iacc, 13, [_VARIANT, ctypes.POINTER(_VARIANT)])
                    get_accState = _vfunc(target_iacc, 14, [_VARIANT, ctypes.POINTER(_VARIANT)])
                    var_role = _VARIANT()
                    hr_role = get_accRole(target_iacc, child_var, ctypes.byref(var_role))
                    var_state = _VARIANT()
                    hr_state = get_accState(target_iacc, child_var, ctypes.byref(var_state))

                    role = var_role.pdispVal if hr_role == 0 else 0
                    state = var_state.pdispVal if hr_state == 0 else 0

                    if release_disp and var_focus.pdispVal:
                        _vfunc(var_focus.pdispVal, 2, [], restype=wintypes.ULONG)(var_focus.pdispVal)

                    if role in (_ROLE_SYSTEM_TEXT, _ROLE_SYSTEM_COMBOBOX):
                        return not bool(state & _STATE_SYSTEM_READONLY)
                    elif role in (_ROLE_SYSTEM_STATICTEXT,):
                        return False
                    elif role == _ROLE_SYSTEM_DOCUMENT:
                        return not bool(state & _STATE_SYSTEM_READONLY)
                finally:
                    _vfunc(p_acc.value, 2, [], restype=wintypes.ULONG)(p_acc.value)
    except Exception:
        pass

    return False


def is_question_text(text: str) -> bool:
    """Return True if the selected text ends with a question mark and is a query."""
    if not text:
        return False
    cleaned = text.strip().rstrip("\"'’”`)\u201d\u2019]")
    if not cleaned.endswith("?"):
        return False
    # Ensure it is at least 3 characters and contains alphanumeric characters
    return len(cleaned) >= 3 and any(ch.isalnum() for ch in cleaned)


def is_notion_input(target_hwnd: int) -> bool:
    """Return True if the selection target is an editable Notion input field."""
    if not target_hwnd:
        return False
    user32 = ctypes.windll.user32
    if not user32.IsWindow(target_hwnd):
        return False

    is_notion = False
    buf = ctypes.create_unicode_buffer(512)
    if user32.GetWindowTextW(target_hwnd, buf, 512):
        if "notion" in buf.value.lower():
            is_notion = True

    if not is_notion:
        cls_buf = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(target_hwnd, cls_buf, 256):
            if "notion" in cls_buf.value.lower():
                is_notion = True

    if not is_notion:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(target_hwnd, ctypes.byref(pid))
        if pid.value:
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if h_proc:
                try:
                    name_buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(512)
                    if kernel32.QueryFullProcessImageNameW(h_proc, 0, name_buf, ctypes.byref(size)):
                        if "notion" in name_buf.value.lower():
                            is_notion = True
                finally:
                    kernel32.CloseHandle(h_proc)

    if not is_notion:
        return False

    return is_editable_input_target(target_hwnd)


