from __future__ import annotations

import ctypes
import math
import time
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal


VK_LBUTTON = 0x01
VK_CONTROL = 0x11
VK_MENU = 0x12
MIN_DRAG_DISTANCE = 8
MAX_DRAG_SECONDS = 8.0
GA_ROOT = 2


@dataclass(frozen=True)
class PointerSample:
    down: bool
    x: int
    y: int
    hwnd: int
    when: float
    blocked_modifier: bool = False


class SelectionGestureTracker:
    """Recognize mouse drags and double-clicks without reacting to ordinary clicks."""

    def __init__(self, double_click_seconds: float = 0.5):
        self.double_click_seconds = max(0.1, double_click_seconds)
        self._pressed = False
        self._start_x = 0
        self._start_y = 0
        self._start_time = 0.0
        self._blocked = False
        self._last_click_time = 0.0
        self._last_click_x = 0
        self._last_click_y = 0
        self._last_click_hwnd = 0
        self._selection_bounds: tuple[int, int, int, int] | None = None

    @property
    def selection_bounds(self) -> tuple[int, int, int, int] | None:
        return self._selection_bounds

    def reset(self) -> None:
        self._pressed = False
        self._blocked = False
        self._last_click_time = 0.0
        self._last_click_hwnd = 0
        self._selection_bounds = None

    def update(self, sample: PointerSample) -> int | None:
        if sample.down:
            if not self._pressed:
                self._selection_bounds = None
                self._pressed = True
                self._start_x = sample.x
                self._start_y = sample.y
                self._start_time = sample.when
                self._blocked = sample.blocked_modifier
            else:
                self._blocked = self._blocked or sample.blocked_modifier
            return None

        if not self._pressed:
            return None

        self._pressed = False
        duration = sample.when - self._start_time
        distance = math.hypot(sample.x - self._start_x, sample.y - self._start_y)
        valid_window = bool(sample.hwnd)
        blocked = self._blocked or sample.blocked_modifier
        self._blocked = False
        if blocked or not valid_window or duration < 0 or duration > MAX_DRAG_SECONDS:
            self._last_click_time = 0.0
            return None

        if distance >= MIN_DRAG_DISTANCE:
            self._last_click_time = 0.0
            self._selection_bounds = (
                min(self._start_x, sample.x) - 8,
                min(self._start_y, sample.y) - 20,
                max(self._start_x, sample.x) + 8,
                max(self._start_y, sample.y) + 20,
            )
            return sample.hwnd

        double_click = (
            self._last_click_hwnd == sample.hwnd
            and 0 < sample.when - self._last_click_time <= self.double_click_seconds
            and math.hypot(
                sample.x - self._last_click_x,
                sample.y - self._last_click_y,
            ) < MIN_DRAG_DISTANCE
        )
        self._remember_click(sample)
        if double_click:
            self._selection_bounds = (
                sample.x - 70,
                sample.y - 22,
                sample.x + 70,
                sample.y + 22,
            )
        return sample.hwnd if double_click else None

    def _remember_click(self, sample: PointerSample) -> None:
        self._last_click_time = sample.when
        self._last_click_x = sample.x
        self._last_click_y = sample.y
        self._last_click_hwnd = sample.hwnd


class SelectionMonitor(QObject):
    selection_gesture = Signal(int, object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._enabled = False
        self._ignored_hwnd = 0
        self._user32 = ctypes.windll.user32
        self._user32.WindowFromPoint.argtypes = [wintypes.POINT]
        self._user32.WindowFromPoint.restype = wintypes.HWND
        self._user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self._user32.GetAncestor.restype = wintypes.HWND
        self._tracker = SelectionGestureTracker(
            self._user32.GetDoubleClickTime() / 1000.0
        )
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)

    def set_ignored_window(self, hwnd: int) -> None:
        self._ignored_hwnd = int(hwnd)

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._tracker.reset()
        if enabled:
            self._timer.start()
        else:
            self._timer.stop()

    def _poll(self) -> None:
        user32 = self._user32
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return
        hovered = user32.WindowFromPoint(point)
        hwnd = int(user32.GetAncestor(hovered, GA_ROOT) or hovered or 0)
        blocked = any(
            user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_CONTROL, VK_MENU)
        )
        target = self._tracker.update(PointerSample(
            down=bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000),
            x=int(point.x),
            y=int(point.y),
            hwnd=hwnd,
            when=time.monotonic(),
            blocked_modifier=blocked,
        ))
        if target:
            # Give the target time to finalize its selection, but preserve the root
            # window observed at button-up. Re-resolving later can pick the window
            # underneath if stacking or the pointer changes during this delay.
            QTimer.singleShot(
                180,
                lambda target=target, bounds=self._tracker.selection_bounds: self._emit_target(
                    target, bounds
                ),
            )

    def _emit_target(
        self,
        target: int,
        bounds: tuple[int, int, int, int] | None,
    ) -> None:
        if not self._enabled:
            return
        if target and target != self._ignored_hwnd:
            self.selection_gesture.emit(target, bounds)
