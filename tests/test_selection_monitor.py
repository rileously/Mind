import unittest

from mind.selection_monitor import PointerSample, SelectionGestureTracker


class SelectionGestureTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = SelectionGestureTracker(double_click_seconds=0.5)

    def sample(
        self,
        down: bool,
        x: int,
        y: int,
        when: float,
        hwnd: int = 100,
        blocked: bool = False,
    ) -> PointerSample:
        return PointerSample(down, x, y, hwnd, when, blocked)

    def test_drag_in_same_window_requests_selection(self):
        self.assertIsNone(self.tracker.update(self.sample(True, 10, 20, 1.0)))
        self.assertEqual(self.tracker.update(self.sample(False, 45, 20, 1.3)), 100)

    def test_ordinary_click_does_not_request_selection(self):
        self.tracker.update(self.sample(True, 10, 20, 1.0))
        self.assertIsNone(self.tracker.update(self.sample(False, 11, 20, 1.1)))

    def test_double_click_requests_selection(self):
        self.tracker.update(self.sample(True, 10, 20, 1.0))
        self.tracker.update(self.sample(False, 10, 20, 1.1))
        self.tracker.update(self.sample(True, 11, 20, 1.25))
        self.assertEqual(self.tracker.update(self.sample(False, 11, 20, 1.35)), 100)

    def test_release_window_is_used_after_click_activation(self):
        self.tracker.update(self.sample(True, 10, 20, 1.0))
        self.assertEqual(
            self.tracker.update(self.sample(False, 45, 20, 1.2, hwnd=200)),
            200,
        )

    def test_modifier_drag_is_ignored(self):
        self.tracker.update(self.sample(True, 10, 20, 2.0, blocked=True))
        self.assertIsNone(self.tracker.update(self.sample(False, 45, 20, 2.2)))

    def test_reset_forgets_a_pending_double_click(self):
        self.tracker.update(self.sample(True, 10, 20, 1.0))
        self.tracker.update(self.sample(False, 10, 20, 1.1))
        self.tracker.reset()
        self.tracker.update(self.sample(True, 10, 20, 1.2))
        self.assertIsNone(self.tracker.update(self.sample(False, 10, 20, 1.3)))

    def test_single_click_tracks_last_single_click(self):
        self.tracker.update(self.sample(True, 10, 20, 1.0))
        result = self.tracker.update(self.sample(False, 11, 20, 1.1, hwnd=300))
        self.assertIsNone(result)
        self.assertIsNotNone(self.tracker.last_single_click)
        self.assertEqual(self.tracker.last_single_click[0], 300)


if __name__ == "__main__":
    unittest.main()
