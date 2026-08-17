"""Watchers fire on a change, not on a state.

Noticing a low battery is easy. Not repeating it every twenty-five seconds for
the next hour is the feature. These tests hold that: a watcher speaks when the
condition becomes true, stays quiet while it stays true, does not flicker when a
reading sits on the threshold, and repeats only when its cooldown says it may.
"""

import unittest

from mind.watchers import (
    BATTERY_FULL,
    BATTERY_LOW,
    DISK_LOW,
    FOLDER_NEW,
    IDLE,
    KINDS,
    MEMORY_HIGH,
    Reading,
    Watcher,
    evaluate,
    from_dict,
    kind_by_key,
    new_watcher,
    to_dict,
    watched_drives,
    watched_folders,
)


HOUR = 3600.0


def battery(percent: int, mains: bool = False) -> Reading:
    return Reading(battery_percent=percent, on_mains=mains)


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.watcher = Watcher(id="w1", kind=BATTERY_LOW, threshold=20)

    def test_it_fires_when_the_condition_becomes_true(self):
        firings, state = evaluate([self.watcher], battery(18), {}, 0)
        self.assertEqual(len(firings), 1)
        self.assertIn("18%", firings[0].message)
        self.assertFalse(state["w1"]["armed"])

    def test_it_stays_quiet_while_the_condition_holds(self):
        firings, state = evaluate([self.watcher], battery(18), {}, 0)
        self.assertEqual(len(firings), 1)
        for minute in range(1, 30):
            firings, state = evaluate([self.watcher], battery(17), state, minute * 60)
            self.assertEqual(firings, [], f"spoke again after {minute} minutes")

    def test_it_says_nothing_before_the_condition_is_true(self):
        firings, _ = evaluate([self.watcher], battery(80), {}, 0)
        self.assertEqual(firings, [])

    def test_a_reading_sitting_on_the_threshold_does_not_flicker(self):
        # Real hardware reports 19, 20, 19, 20. Without the re-arm margin this
        # would fire on every other tick.
        _, state = evaluate([self.watcher], battery(19), {}, 0)
        for index, percent in enumerate((20, 19, 20, 21, 19, 22)):
            firings, state = evaluate([self.watcher], battery(percent), state, index * 60)
            self.assertEqual(firings, [], f"fired again at {percent}%")

    def test_it_fires_again_once_the_reading_has_properly_recovered(self):
        _, state = evaluate([self.watcher], battery(18), {}, 0)
        # Past the threshold and the margin: charged up again.
        _, state = evaluate([self.watcher], battery(40), state, 60)
        firings, _ = evaluate([self.watcher], battery(15), state, 120)
        self.assertEqual(len(firings), 1)

    def test_a_condition_that_persists_repeats_only_after_the_cooldown(self):
        watcher = Watcher(id="w1", kind=DISK_LOW, threshold=20, target="C:\\", cooldown_minutes=60)
        low = Reading(free_gb={"C:\\": 8.0})
        firings, state = evaluate([watcher], low, {}, 0)
        self.assertEqual(len(firings), 1)
        firings, state = evaluate([watcher], low, state, 59 * 60)
        self.assertEqual(firings, [])
        firings, state = evaluate([watcher], low, state, 61 * 60)
        self.assertEqual(len(firings), 1, "should repeat once the hour is up")

    def test_a_cooldown_of_zero_means_say_it_once_only(self):
        watcher = Watcher(id="w1", kind=DISK_LOW, threshold=20, target="C:\\", cooldown_minutes=0)
        low = Reading(free_gb={"C:\\": 8.0})
        firings, state = evaluate([watcher], low, {}, 0)
        self.assertEqual(len(firings), 1)
        firings, _ = evaluate([watcher], low, state, 10 * HOUR)
        self.assertEqual(firings, [])


class KindTests(unittest.TestCase):
    def test_a_low_battery_on_mains_is_not_worth_reporting(self):
        watcher = Watcher(id="w1", kind=BATTERY_LOW, threshold=20)
        firings, _ = evaluate([watcher], battery(10, mains=True), {}, 0)
        self.assertEqual(firings, [])

    def test_a_full_battery_only_matters_while_it_is_plugged_in(self):
        watcher = Watcher(id="w1", kind=BATTERY_FULL, threshold=95)
        firings, _ = evaluate([watcher], battery(98, mains=False), {}, 0)
        self.assertEqual(firings, [])
        firings, _ = evaluate([watcher], battery(98, mains=True), {}, 0)
        self.assertEqual(len(firings), 1)
        self.assertIn("unplug", firings[0].message.lower())

    def test_disk_and_memory_compare_in_the_direction_that_matters(self):
        disk = Watcher(id="d", kind=DISK_LOW, threshold=20, target="C:\\")
        memory = Watcher(id="m", kind=MEMORY_HIGH, threshold=90)
        reading = Reading(free_gb={"C:\\": 5.0}, memory_used_percent=95)
        firings, _ = evaluate([disk, memory], reading, {}, 0)
        self.assertEqual({f.watcher_id for f in firings}, {"d", "m"})

        healthy = Reading(free_gb={"C:\\": 500.0}, memory_used_percent=30)
        firings, _ = evaluate([disk, memory], healthy, {}, 0)
        self.assertEqual(firings, [])

    def test_idle_fires_once_and_re_arms_when_you_come_back(self):
        watcher = Watcher(id="w1", kind=IDLE, threshold=30)
        firings, state = evaluate([watcher], Reading(idle_minutes=31), {}, 0)
        self.assertEqual(len(firings), 1)
        firings, state = evaluate([watcher], Reading(idle_minutes=45), state, 60)
        self.assertEqual(firings, [])
        # Back at the keyboard, then away again.
        _, state = evaluate([watcher], Reading(idle_minutes=0), state, 120)
        firings, _ = evaluate([watcher], Reading(idle_minutes=31), state, 180)
        self.assertEqual(len(firings), 1)

    def test_a_reading_that_cannot_be_taken_says_nothing(self):
        # A desktop with no battery, or a drive that has been unplugged.
        watcher = Watcher(id="w1", kind=BATTERY_LOW, threshold=20)
        firings, _ = evaluate([watcher], Reading(battery_percent=None), {}, 0)
        self.assertEqual(firings, [])
        disk = Watcher(id="d", kind=DISK_LOW, threshold=20, target="Z:\\")
        firings, _ = evaluate([disk], Reading(free_gb={}), {}, 0)
        self.assertEqual(firings, [])


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.watcher = Watcher(id="f1", kind=FOLDER_NEW, target=r"C:\Downloads")

    def test_the_first_look_is_not_news(self):
        # Otherwise creating the watcher announces everything already in there.
        reading = Reading(folder_files={r"C:\Downloads": ("a.pdf", "b.png")})
        firings, state = evaluate([self.watcher], reading, {}, 0)
        self.assertEqual(firings, [])
        self.assertEqual(state["f1"]["seen"], ["a.pdf", "b.png"])

    def test_a_new_file_is_reported_by_name(self):
        reading = Reading(folder_files={r"C:\Downloads": ("a.pdf",)})
        _, state = evaluate([self.watcher], reading, {}, 0)
        arrived = Reading(folder_files={r"C:\Downloads": ("a.pdf", "invoice.pdf")})
        firings, state = evaluate([self.watcher], arrived, state, 60)
        self.assertEqual(len(firings), 1)
        self.assertIn("invoice.pdf", firings[0].message)
        self.assertIn("1 new file", firings[0].message)

    def test_many_files_at_once_are_one_message(self):
        _, state = evaluate([self.watcher], Reading(folder_files={r"C:\Downloads": ()}), {}, 0)
        many = tuple(f"file{i}.txt" for i in range(9))
        firings, _ = evaluate(
            [self.watcher], Reading(folder_files={r"C:\Downloads": many}), state, 60
        )
        self.assertEqual(len(firings), 1)
        self.assertIn("9 new files", firings[0].message)
        self.assertIn("and 4 more", firings[0].message)

    def test_a_file_removed_is_not_reported_and_does_not_re_announce(self):
        start = Reading(folder_files={r"C:\Downloads": ("a.pdf", "b.pdf")})
        _, state = evaluate([self.watcher], start, {}, 0)
        gone = Reading(folder_files={r"C:\Downloads": ("a.pdf",)})
        firings, state = evaluate([self.watcher], gone, state, 60)
        self.assertEqual(firings, [])
        back = Reading(folder_files={r"C:\Downloads": ("a.pdf", "b.pdf")})
        firings, _ = evaluate([self.watcher], back, state, 120)
        self.assertEqual(len(firings), 1, "a file returning is new again")


class PausedTests(unittest.TestCase):
    def test_a_paused_watcher_says_nothing(self):
        watcher = Watcher(id="w1", kind=BATTERY_LOW, threshold=20, enabled=False)
        firings, _ = evaluate([watcher], battery(5), {}, 0)
        self.assertEqual(firings, [])

    def test_pausing_does_not_lose_where_it_stood(self):
        # Resuming should not fire about a condition it had already reported.
        watcher = Watcher(id="w1", kind=BATTERY_LOW, threshold=20)
        _, state = evaluate([watcher], battery(18), {}, 0)
        paused = Watcher(id="w1", kind=BATTERY_LOW, threshold=20, enabled=False)
        _, state = evaluate([paused], battery(18), state, 60)
        firings, _ = evaluate([watcher], battery(18), state, 120)
        self.assertEqual(firings, [])


class StorageTests(unittest.TestCase):
    def test_a_watcher_survives_a_round_trip(self):
        watcher = new_watcher(DISK_LOW, 15, "D:\\")
        restored = from_dict(to_dict(watcher))
        self.assertEqual(restored, watcher)

    def test_a_kind_this_build_does_not_know_is_dropped(self):
        # Better than a row that looks like it works and never fires.
        self.assertIsNone(from_dict({"kind": "sunspots", "threshold": 3}))
        self.assertIsNone(from_dict("not a watcher"))

    def test_unreadable_numbers_are_dropped_rather_than_guessed(self):
        self.assertIsNone(from_dict({"kind": BATTERY_LOW, "threshold": "very low"}))

    def test_a_new_watcher_starts_from_the_kind_s_own_default(self):
        self.assertEqual(new_watcher(BATTERY_LOW).threshold, kind_by_key(BATTERY_LOW).default_threshold)

    def test_every_kind_has_a_label_and_a_sensible_default(self):
        for kind in KINDS:
            self.assertTrue(kind.label)
            self.assertGreaterEqual(kind.rearm_margin, 0)

    def test_only_the_places_being_watched_are_collected(self):
        watchers = [
            Watcher(id="a", kind=FOLDER_NEW, target=r"C:\In"),
            Watcher(id="b", kind=FOLDER_NEW, target=r"C:\In"),
            Watcher(id="c", kind=FOLDER_NEW, target=r"C:\Out", enabled=False),
            Watcher(id="d", kind=DISK_LOW, target="C:\\", threshold=10),
        ]
        self.assertEqual(watched_folders(watchers), [r"C:\In"])
        self.assertEqual(watched_drives(watchers), ["C:\\"])


if __name__ == "__main__":
    unittest.main()


class BridgeIntegrationTests(unittest.TestCase):
    """The tick, the storage, and the /watch panel, against a fake chat."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from mind.config_store import ConfigStore
        from mind.telegram_bridge import TelegramBridge
        from tests.test_telegram_menu_flow import FakeClient

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ConfigStore(root=self.root / "config")
        self.bridge = TelegramBridge(self.store)
        self.client = FakeClient()
        self.config = {
            "watchers_enabled": True,
            "telegram_allowed_chat_ids": [7],
        }

    def save(self, *watchers):
        self.store.save_watchers([to_dict(w) for w in watchers])

    def test_a_watcher_survives_being_written_and_read(self):
        self.save(new_watcher(DISK_LOW, 12, "C:\\"))
        restored = self.store.load_watchers()
        self.assertEqual(len(restored), 1)
        self.assertEqual(from_dict(restored[0]).target, "C:\\")

    def test_nothing_is_sent_when_watchers_are_switched_off(self):
        self.save(Watcher(id="w1", kind=MEMORY_HIGH, threshold=1))
        self.bridge._check_watchers(self.client, {"telegram_allowed_chat_ids": [7]})
        self.assertEqual(self.client.sent, [])

    def test_an_alert_reaches_every_allowed_chat(self):
        self.save(Watcher(id="w1", kind=MEMORY_HIGH, threshold=0))
        self.config["telegram_allowed_chat_ids"] = [7, 9]
        self.bridge._check_watchers(self.client, self.config)
        self.assertEqual(len(self.client.sent), 2)
        self.assertIn("Memory", self.client.sent[0]["text"])

    def test_it_does_not_repeat_on_the_next_tick(self):
        # The whole point of the feature: the loop wakes every twenty-five
        # seconds, and this must not become a message every twenty-five seconds.
        self.save(Watcher(id="w1", kind=MEMORY_HIGH, threshold=0))
        for _ in range(5):
            self.bridge._check_watchers(self.client, self.config)
        self.assertEqual(len(self.client.sent), 1)

    def test_an_alert_is_content_and_carries_no_buttons(self):
        # It must never be treated as a panel, or the next one would delete it.
        self.save(Watcher(id="w1", kind=MEMORY_HIGH, threshold=0))
        self.bridge._check_watchers(self.client, self.config)
        self.assertIsNone(self.client.sent[0]["markup"])
        self.assertEqual(self.client.deleted, [])

    def test_a_folder_watcher_reports_a_file_that_appears(self):
        folder = self.root / "inbox"
        folder.mkdir()
        self.save(Watcher(id="f1", kind=FOLDER_NEW, target=str(folder)))
        self.bridge._check_watchers(self.client, self.config)
        self.assertEqual(self.client.sent, [], "the first look is not news")
        (folder / "arrived.pdf").write_text("x", encoding="utf-8")
        self.bridge._check_watchers(self.client, self.config)
        self.assertEqual(len(self.client.sent), 1)
        self.assertIn("arrived.pdf", self.client.sent[0]["text"])

    def test_only_the_watched_folders_are_scanned(self):
        # This runs every twenty-five seconds; walking anywhere else would be a
        # waste all day long.
        watched = self.root / "watched"
        other = self.root / "other"
        watched.mkdir()
        other.mkdir()
        self.save(Watcher(id="f1", kind=FOLDER_NEW, target=str(watched)))
        reading = self.bridge._watcher_reading(self.bridge._load_watchers())
        self.assertEqual(list(reading.folder_files), [str(watched)])

    def test_a_folder_that_has_gone_does_not_stop_the_tick(self):
        self.save(Watcher(id="f1", kind=FOLDER_NEW, target=str(self.root / "missing")))
        self.bridge._check_watchers(self.client, self.config)
        self.assertEqual(self.client.sent, [])

    def test_the_watch_panel_lists_them_with_a_way_to_pause(self):
        self.save(Watcher(id="w1", kind=BATTERY_LOW, threshold=20))
        self.bridge._send_watcher_panel(self.client, 7, self.config)
        self.assertIn("Watching this PC", self.client.sent[-1]["text"])
        self.assertIn("Pause", str(self.client.sent[-1]["markup"]))

    def test_tapping_a_watcher_pauses_it_and_the_change_is_saved(self):
        self.save(Watcher(id="w1", kind=BATTERY_LOW, threshold=20))
        self.bridge._send_watcher_panel(self.client, 7, self.config)
        message = self.client.sent[-1]["id"]
        self.bridge._handle_watcher_tap(self.client, 7, "cb", message, 0, self.config)
        self.assertFalse(from_dict(self.store.load_watchers()[0]).enabled)
        self.assertIn("Resume", str(self.client.edited[-1]["markup"]))

    def test_a_tap_on_a_list_that_has_changed_asks_for_a_fresh_one(self):
        self.bridge._handle_watcher_tap(self.client, 7, "cb", 500, 4, self.config)
        self.assertTrue(any("again" in text for text in self.client.answered))

    def test_the_panel_says_what_to_do_when_there_are_none(self):
        self.bridge._send_watcher_panel(self.client, 7, self.config)
        self.assertIn("No watchers yet", self.client.sent[-1]["text"])

    def test_the_panel_says_when_the_feature_is_off(self):
        self.save(Watcher(id="w1", kind=BATTERY_LOW, threshold=20))
        self.bridge._send_watcher_panel(self.client, 7, {"watchers_enabled": False})
        self.assertIn("switched off", self.client.sent[-1]["text"])


class NotificationsPageTests(unittest.TestCase):
    """The switch sits with the watchers, not two pages away."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import tempfile
        from pathlib import Path
        from mind.config_store import ConfigStore
        from mind.main_window import NotificationsPage

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.store.save({**self.store.load(), "telegram_enabled": True})
        self.page = NotificationsPage(self.store)
        self.addCleanup(self.page.deleteLater)

    def test_the_switch_starts_off_and_says_so(self):
        self.assertFalse(self.page.enabled_switch.isChecked())
        self.assertIn("off", self.page.state_label.text().lower())

    def test_flicking_the_switch_starts_watching(self):
        # The failure this fixes: watchers created, the feature built, and
        # nothing happening because the switch was on another page.
        self.page.enabled_switch.setChecked(True)
        self.assertTrue(bool(self.store.load().get("watchers_enabled")))
        self.assertIn("watch", self.page.state_label.text().lower())

    def test_it_can_be_switched_off_again(self):
        self.page.enabled_switch.setChecked(True)
        self.page.enabled_switch.setChecked(False)
        self.assertFalse(bool(self.store.load().get("watchers_enabled")))

    def test_loading_the_saved_value_does_not_write_it_back(self):
        from mind.main_window import NotificationsPage

        self.store.save({**self.store.load(), "watchers_enabled": True})
        page = NotificationsPage(self.store)
        self.addCleanup(page.deleteLater)
        self.assertTrue(page.enabled_switch.isChecked())
        self.assertTrue(bool(self.store.load().get("watchers_enabled")))

    def test_the_switch_is_unavailable_without_the_bridge(self):
        from mind.main_window import NotificationsPage

        self.store.save({**self.store.load(), "telegram_enabled": False})
        page = NotificationsPage(self.store)
        self.addCleanup(page.deleteLater)
        self.assertFalse(page.enabled_switch.isEnabled())
        self.assertIn("bridge is off", page.state_label.text().lower())

    def test_being_on_with_nothing_to_watch_is_said_plainly(self):
        self.page.enabled_switch.setChecked(True)
        self.assertIn("nothing to watch", self.page.state_label.text().lower())

    def test_a_watcher_added_on_the_page_is_stored(self):
        self.page.watchers.append(new_watcher(IDLE, 15))
        self.page._save()
        self.assertEqual(len(self.store.load_watchers()), 1)
        self.assertEqual(self.page.table.rowCount(), 1)


class AlertFileButtonTests(unittest.TestCase):
    """An alert about a new file offers the file."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from mind.config_store import ConfigStore
        from mind.telegram_bridge import TelegramBridge
        from tests.test_telegram_menu_flow import FakeClient

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "watched"
        self.folder.mkdir()
        self.store = ConfigStore(root=self.root / "config")
        self.store.save_watchers(
            [to_dict(Watcher(id="f1", kind=FOLDER_NEW, target=str(self.folder)))]
        )
        self.bridge = TelegramBridge(self.store)
        self.client = FakeClient()
        self.config = {"watchers_enabled": True, "telegram_allowed_chat_ids": [7]}

    def arrive(self, name: str, data: bytes = b"x") -> None:
        self.bridge._check_watchers(self.client, self.config)  # first look
        (self.folder / name).write_bytes(data)
        self.bridge._check_watchers(self.client, self.config)

    def test_the_alert_carries_a_button_for_the_file(self):
        self.arrive("photo.jpg")
        self.assertIsNotNone(self.client.sent[-1]["markup"])
        self.assertIn("View", str(self.client.sent[-1]["markup"]))

    def test_tapping_it_sends_an_image_as_a_photo(self):
        self.arrive("photo.jpg")
        message = self.client.sent[-1]["id"]
        self.bridge._handle_watched_file_tap(self.client, 7, "cb", message, 0, self.config)
        self.assertEqual(self.client.photos[-1]["caption"], "photo.jpg")

    def test_anything_else_arrives_as_a_file(self):
        self.arrive("notes.pdf")
        message = self.client.sent[-1]["id"]
        self.bridge._handle_watched_file_tap(self.client, 7, "cb", message, 0, self.config)
        self.assertEqual(self.client.documents[-1]["caption"], "notes.pdf")

    def test_a_file_deleted_before_the_tap_says_so(self):
        self.arrive("gone.txt")
        (self.folder / "gone.txt").unlink()
        message = self.client.sent[-1]["id"]
        self.bridge._handle_watched_file_tap(self.client, 7, "cb", message, 0, self.config)
        self.assertTrue(any("gone" in text.lower() for text in self.client.answered))
        self.assertEqual(self.client.documents, [])

    def test_an_alert_from_before_a_restart_cannot_fetch(self):
        self.bridge._handle_watched_file_tap(self.client, 7, "cb", 999, 0, self.config)
        self.assertTrue(any("too old" in text.lower() for text in self.client.answered))

    def test_a_button_only_ever_means_a_file_in_the_watched_folder(self):
        # The name is remembered, not a path, and it is resolved inside the
        # folder that was being watched.
        self.arrive("safe.txt")
        message = self.client.sent[-1]["id"]
        self.bridge._watched_files[(7, message)] = (str(self.folder), ("..\..\secret.txt",))
        self.bridge._handle_watched_file_tap(self.client, 7, "cb", message, 0, self.config)
        self.assertEqual(self.client.documents, [])
        self.assertEqual(self.client.photos, [])

    def test_alerts_that_name_nothing_carry_no_buttons(self):
        self.store.save_watchers([to_dict(Watcher(id="m", kind=MEMORY_HIGH, threshold=0))])
        bridge_client = self.client
        self.bridge._check_watchers(bridge_client, self.config)
        self.assertIsNone(bridge_client.sent[-1]["markup"])
