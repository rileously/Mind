"""The Wi-Fi devices page: what it shows, and what it refuses to do quietly.

The page is built for real against a real QApplication, because the failure
worth catching is the one that only happens when it is constructed - a row put
together wrongly raises nowhere else, and the result is an executable that
cannot start.

The scanner is a stand-in. Nothing here touches a network or a router: what is
being checked is the page's own reasoning - which device a click means, which
devices a network switch is about to cut off, and when it declines to act.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QObject, QThread, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from mind.config_store import ConfigStore  # noqa: E402
from mind.main_window import NetworkDevicesPage  # noqa: E402
from mind.network_devices import Device  # noqa: E402
from mind.router_client import Ssid  # noqa: E402


NOW = time.time()


def phone(mac: str, ip: str, name: str = "", network: str = "", **rest) -> Device:
    return Device(
        mac=mac,
        ip=ip,
        custom_name=name,
        network=network,
        last_seen=NOW,
        online=rest.pop("online", True),
        **rest,
    )


class FakeScanner(QObject):
    """The scanner's shape, with none of its behaviour."""

    devices_changed = Signal(list)
    blocked_changed = Signal(list)
    networks_changed = Signal(list)
    scanning = Signal(bool)

    def __init__(self, devices=(), blocked=(), networks=()):
        super().__init__()
        self.devices = list(devices)
        self.blocked = set(blocked)
        self.networks = list(networks)
        self.scans = 0
        self.forgotten: list[str] = []

    def scan_now(self):
        self.scans += 1

    def forget(self, mac):
        self.forgotten.append(mac)

    def set_interval(self, seconds):
        pass

    def start(self, *args):
        pass

    def stop(self):
        pass


NETWORKS = (
    Ssid(1, "Home 2.4G", "2.4GHz", domain="...WLANConfiguration.1", enabled=True),
    Ssid(2, "Home kids", "2.4GHz", domain="...WLANConfiguration.2", enabled=True),
    Ssid(6, "Guest", "5GHz", domain="...WLANConfiguration.6", enabled=False),
)

DEVICES = (
    phone("a2-27-ec-61-6a-a6", "192.168.18.12", "Shayan's phone", "SSID2", kind="Android 13"),
    phone("00-08-22-33-1d-51", "192.168.18.26", network="SSID2", kind="Android 15"),
    phone("90-de-80-77-53-37", "192.168.18.7", "Desktop", kind="Windows"),
    phone("11-22-33-44-55-66", "192.168.18.40", "Old tablet", "SSID1", online=False),
)


class PageTestCase(unittest.TestCase):
    """Every question the page asks is answered here rather than by a person.

    Without this a single setChecked() in a test opens a real modal dialog and
    the suite waits for a click that never comes - which is also the honest
    shape of the thing being tested: moving a switch asks before it acts.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.asked: list[tuple[str, str]] = []
        self.warned: list[tuple[str, str]] = []
        self.answer = QMessageBox.No

        def question(_parent, title, text, *args, **kwargs):
            self.asked.append((title, text))
            return self.answer

        def warning(_parent, title, text, *args, **kwargs):
            self.warned.append((title, text))
            return QMessageBox.Ok

        for name, stand_in in (("question", question), ("warning", warning)):
            patch = unittest.mock.patch.object(QMessageBox, name, staticmethod(stand_in))
            patch.start()
            self.addCleanup(patch.stop)
        # Nothing here may reach for a router. The worker is built as usual so
        # that what the page asked of it can be read back, and then not run.
        started = unittest.mock.patch.object(QThread, "start", lambda _self: None)
        started.start()
        self.addCleanup(started.stop)

    def build(self, devices=DEVICES, blocked=(), networks=NETWORKS, router=True):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = ConfigStore(root=Path(self.temp.name) / "config")
        config = store.load()
        config["network_scan_enabled"] = True
        if router:
            config["router_address"] = "192.168.18.1"
            config["router_username"] = "Epuser"
            config = store.set_router_password(config, "not-a-real-password")
        store.save(config)
        scanner = FakeScanner(devices, blocked, networks)
        page = NetworkDevicesPage(store, scanner)
        self.addCleanup(page.deleteLater)
        return page, scanner, store

    def names_shown(self, page) -> list[str]:
        return [device.display_name for device in page.shown]

    def row_of(self, page, name: str) -> int:
        """The row a device is drawn on, found by reading the table.

        Not by counting into the list behind it: sorting is on, so the third
        row is not the third device, and a test that assumed otherwise would
        check the wrong row and pass anyway.
        """
        column = page.COLUMNS.index("Name")
        for row in range(page.table.rowCount()):
            if page.table.item(row, column).text() == name:
                return row
        raise AssertionError(f"{name!r} is not in the table")


class ItBuildsTests(PageTestCase):
    def test_the_page_builds_with_a_router_and_devices(self):
        page, _scanner, _store = self.build()
        self.assertEqual(len(page.shown), len(DEVICES))

    def test_the_page_builds_with_nothing_at_all(self):
        page, _scanner, _store = self.build(devices=(), networks=(), router=False)
        self.assertEqual(page.shown, [])
        self.assertTrue(page.empty_label.isVisible() or page.empty_label.text())

    def test_an_empty_list_says_what_to_do_rather_than_nothing(self):
        page, _scanner, _store = self.build(devices=(), networks=())
        self.assertIn("Turn the switch on", page.empty_label.text())


class WhatTheRowsSayTests(PageTestCase):
    def test_a_phone_that_named_itself_to_nobody_is_not_called_after_the_wifi(self):
        # The bug this page exists downstream of: several devices all reading
        # "SSID2" with block buttons that could not be told apart.
        page, _scanner, _store = self.build()
        self.assertNotIn("SSID2", self.names_shown(page))

    def test_what_a_device_is_shows_beside_its_name_and_never_as_it(self):
        page, _scanner, _store = self.build()
        row = self.row_of(page, "Device 26")
        kind = page.table.item(row, page.COLUMNS.index("What it is")).text()
        self.assertEqual(kind, "Android 15")

    def test_a_device_says_which_network_it_is_on_by_that_networks_name(self):
        page, _scanner, _store = self.build()
        row = self.row_of(page, "Shayan's phone")
        self.assertEqual(page.table.item(row, page.COLUMNS.index("Network")).text(), "Home kids")

    def test_a_wired_device_is_not_claimed_for_any_network(self):
        page, _scanner, _store = self.build()
        row = self.row_of(page, "Desktop")
        self.assertEqual(page.table.item(row, page.COLUMNS.index("Network")).text(), "—")

    def test_blocked_beats_online(self):
        page, _scanner, _store = self.build(blocked=["a2-27-ec-61-6a-a6"])
        row = self.row_of(page, "Shayan's phone")
        self.assertEqual(page.table.item(row, page.COLUMNS.index("Status")).text(), "Blocked")


class FindingOneDeviceTests(PageTestCase):
    def test_the_search_narrows_by_name(self):
        page, _scanner, _store = self.build()
        page.search.setText("shayan")
        self.assertEqual(self.names_shown(page), ["Shayan's phone"])

    def test_the_search_narrows_by_address(self):
        page, _scanner, _store = self.build()
        page.search.setText("18.26")
        self.assertEqual(self.names_shown(page), ["Device 26"])

    def test_the_search_finds_a_device_by_the_network_it_is_on(self):
        page, _scanner, _store = self.build()
        page.search.setText("kids")
        self.assertEqual(len(page.shown), 2)

    def test_the_filter_keeps_only_what_is_online(self):
        page, _scanner, _store = self.build()
        page.filter.setCurrentIndex(page.filter.findData("online"))
        page._redraw()
        self.assertNotIn("Old tablet", self.names_shown(page))

    def test_the_filter_keeps_only_what_is_blocked(self):
        page, _scanner, _store = self.build(blocked=["a2-27-ec-61-6a-a6"])
        page.filter.setCurrentIndex(page.filter.findData("blocked"))
        page._redraw()
        self.assertEqual(self.names_shown(page), ["Shayan's phone"])

    def test_matching_nothing_says_so_rather_than_looking_empty(self):
        page, _scanner, _store = self.build()
        page.search.setText("no such device")
        self.assertEqual(page.shown, [])
        self.assertIn("match that", page.empty_label.text())

    def test_the_count_says_it_is_showing_a_subset(self):
        page, _scanner, _store = self.build()
        page.search.setText("shayan")
        self.assertIn("Showing 1 of 4", page.status_label.text())


class WhichDeviceIsSelectedTests(PageTestCase):
    """Sorting moves rows. The selection must follow the device, not the row."""

    def test_the_selected_device_is_the_one_written_in_the_row(self):
        page, _scanner, _store = self.build()
        page.table.selectRow(0)
        first = page.table.item(0, page.COLUMNS.index("MAC address")).text()
        self.assertEqual(page._selected().mac, first)

    def test_sorting_does_not_change_which_device_a_row_means(self):
        page, _scanner, _store = self.build()
        page.table.sortItems(page.COLUMNS.index("Name"))
        page.table.selectRow(0)
        shown = page.table.item(0, page.COLUMNS.index("Name")).text()
        self.assertEqual(page._selected().display_name, shown)

    def test_selecting_by_address_lands_on_that_device_under_any_sort(self):
        # Sorting moves rows. Selecting by position put the highlight - and
        # every button that names the selection - on a different phone.
        page, _scanner, _store = self.build()
        for column in ("Name", "IP address", "Status"):
            page.table.sortItems(page.COLUMNS.index(column))
            page._select("a2-27-ec-61-6a-a6")
            self.assertEqual(page._selected().mac, "a2-27-ec-61-6a-a6")
            self.assertEqual(page._selected().display_name, "Shayan's phone")

    def test_the_rows_are_in_the_order_the_header_claims(self):
        # Rows are rewritten with sorting off and it does not re-apply itself,
        # so the arrow would promise an order the rows did not follow.
        page, _scanner, _store = self.build()
        page.table.sortItems(page.COLUMNS.index("Name"))
        page._redraw()
        column = page.COLUMNS.index("Name")
        drawn = [
            page.table.item(row, column).text()
            for row in range(page.table.rowCount())
        ]
        self.assertEqual(drawn, sorted(drawn))

    def test_the_selection_survives_a_redraw(self):
        page, _scanner, _store = self.build()
        page._select("a2-27-ec-61-6a-a6")
        page._redraw()
        self.assertEqual(page._selected().mac, "a2-27-ec-61-6a-a6")

    def test_the_buttons_name_the_device_they_would_act_on(self):
        page, _scanner, _store = self.build()
        page._select("a2-27-ec-61-6a-a6")
        page._sync_actions()
        self.assertIn("Shayan's phone", page.block_button.text())
        self.assertIn("Shayan's phone", page.selection_label.text())

    def test_with_nothing_selected_it_asks_for_one(self):
        page, _scanner, _store = self.build()
        page.table.clearSelection()
        page.table.setCurrentCell(-1, -1)
        page._sync_actions()
        self.assertFalse(page.block_button.isEnabled())
        self.assertIn("Pick a device", page.selection_label.text())


class TheNetworksTests(PageTestCase):
    def test_every_network_gets_a_switch_including_the_ones_that_are_off(self):
        page, _scanner, _store = self.build()
        self.assertEqual(sorted(page._network_switches), [1, 2, 6])

    def test_each_switch_starts_where_the_router_has_it(self):
        page, _scanner, _store = self.build()
        self.assertTrue(page._network_switches[2].isChecked())
        self.assertFalse(page._network_switches[6].isChecked())

    def test_drawing_the_switches_does_not_switch_anything(self):
        # A switch set to match the router must not read as a click.
        page, scanner, _store = self.build()
        self.assertEqual(scanner.scans, 0)
        self.assertFalse(page._busy_networks)

    def test_a_network_knows_which_devices_are_on_it(self):
        page, _scanner, _store = self.build()
        kids = next(item for item in page.networks if item.index == 2)
        self.assertEqual(len(page._devices_on(kids)), 2)

    def test_a_device_that_is_offline_is_not_counted_as_on_a_network(self):
        page, _scanner, _store = self.build()
        home = next(item for item in page.networks if item.index == 1)
        self.assertEqual(page._devices_on(home), [])

    def test_asking_for_the_state_it_is_already_in_does_nothing(self):
        page, _scanner, _store = self.build()
        page._switch_network(2, True)  # already on
        self.assertFalse(page._busy_networks)

    def test_a_network_the_router_does_not_have_is_ignored(self):
        page, _scanner, _store = self.build()
        page._switch_network(9, False)
        self.assertFalse(page._busy_networks)

    def test_turning_one_off_names_the_devices_it_would_cut_off(self):
        # "Turn this off?" and "this disconnects Shayan's phone" are different
        # questions, and only one of them can be answered.
        page, _scanner, _store = self.build()
        page._switch_network(2, False)
        _title, text = self.asked[-1]
        self.assertIn("Home kids", text)
        self.assertIn("Shayan's phone", text)

    def test_a_network_with_nothing_on_it_says_so_rather_than_listing_nobody(self):
        # Guest, as it would be with nothing joined to it.
        page, _scanner, _store = self.build(
            networks=(Ssid(6, "Guest", "5GHz", domain="...6", enabled=True),)
        )
        page._switch_network(6, False)
        _title, text = self.asked[-1]
        self.assertIn("Nothing is on it", text)

    def test_answering_no_changes_nothing_and_puts_the_switch_back(self):
        page, _scanner, _store = self.build()
        self.answer = QMessageBox.No
        page._network_switches[2].setChecked(False)
        self.assertTrue(page._network_switches[2].isChecked())
        self.assertFalse(page._busy_networks)

    def test_turning_one_back_on_is_not_worth_asking_about(self):
        # Nothing is lost by a network coming back, so there is no question.
        page, _scanner, _store = self.build()
        page._switch_network(6, True)
        self.assertEqual(self.asked, [])

    def test_a_click_on_a_switch_is_a_question_before_it_is_a_change(self):
        page, _scanner, _store = self.build()
        self.answer = QMessageBox.No
        page._network_switches[2].setChecked(False)
        self.assertEqual(len(self.asked), 1)

    def test_the_networks_card_is_hidden_when_there_is_no_router_to_ask(self):
        page, _scanner, _store = self.build(networks=(), router=False)
        self.assertEqual(page.networks, [])
        self.assertIn("Sign in to the router", page.networks_note.text())


class ThisPcIsNotCutOffTests(PageTestCase):
    """Neither switch may cut the connection that undoes it."""

    def test_the_network_this_pc_is_on_is_recognised(self):
        page, _scanner, _store = self.build()
        here = phone("aa-bb-cc-dd-ee-ff", "10.0.0.1", "This PC", "SSID2")
        page.devices = [here]
        kids = next(item for item in page.networks if item.index == 2)
        with unittest.mock.patch("mind.main_window.local_ipv4", return_value="10.0.0.1"):
            self.assertTrue(page._own_network(kids))

    def test_the_network_this_pc_is_on_is_refused_rather_than_asked_about(self):
        page, _scanner, _store = self.build()
        page.devices = [phone("aa-bb-cc-dd-ee-ff", "10.0.0.1", "This PC", "SSID2")]
        self.answer = QMessageBox.Yes
        with unittest.mock.patch("mind.main_window.local_ipv4", return_value="10.0.0.1"):
            page._switch_network(2, False)
        self.assertFalse(page._busy_networks)
        self.assertTrue(self.warned, "it says why rather than doing nothing")
        self.assertIn("this PC is on", self.warned[-1][1])

    def test_a_network_this_pc_is_not_on_is_fair_game(self):
        page, _scanner, _store = self.build()
        page.devices = [phone("aa-bb-cc-dd-ee-ff", "10.0.0.1", "This PC", "SSID1")]
        kids = next(item for item in page.networks if item.index == 2)
        with unittest.mock.patch("mind.main_window.local_ipv4", return_value="10.0.0.1"):
            self.assertFalse(page._own_network(kids))

    def test_with_no_address_of_its_own_nothing_is_claimed(self):
        page, _scanner, _store = self.build()
        kids = next(item for item in page.networks if item.index == 2)
        with unittest.mock.patch("mind.main_window.local_ipv4", return_value=""):
            self.assertFalse(page._own_network(kids))


class WithoutARouterTests(PageTestCase):
    """The scan needs no router. The two things that write to one do."""

    def test_the_list_still_works(self):
        page, _scanner, _store = self.build(networks=(), router=False)
        self.assertEqual(len(page.shown), len(DEVICES))

    def test_blocking_is_not_offered(self):
        page, _scanner, _store = self.build(networks=(), router=False)
        page._select("a2-27-ec-61-6a-a6")
        page._sync_actions()
        self.assertFalse(page.block_button.isEnabled())
        self.assertIn("Sign in to it first", page.block_button.toolTip())

    def test_the_setup_details_are_open_until_they_are_filled_in(self):
        page, _scanner, _store = self.build(networks=(), router=False)
        self.assertTrue(page.router_details.isVisibleTo(page))

    def test_they_fold_away_once_the_router_is_known(self):
        page, _scanner, _store = self.build()
        self.assertFalse(page.router_details.isVisibleTo(page))
        self.assertIn("192.168.18.1", page.router_summary.text())


class WhenTheRouterRefusesTests(PageTestCase):
    def test_a_refusal_is_said_on_the_page_rather_than_in_a_box(self):
        # A dialog takes the message away with it when it is closed, and there
        # is nothing to do about a refusal until it has been read twice.
        page, scanner, _store = self.build()
        page._block_finished(False, "Its filter list may be full.")
        self.assertIn("filter list may be full", page.router_status.text())
        self.assertEqual(scanner.scans, 1, "the router is still asked who is blocked")

    def test_a_failed_network_switch_puts_the_switch_back(self):
        # The switch moved when it was clicked and the change never happened.
        # Leaving it where the click put it would show a network as off while
        # the phones on it carry on working.
        page, _scanner, _store = self.build()
        page._network_switched(False, "The router refused the change (403).", [])
        self.assertTrue(page._network_switches[2].isChecked())
        self.assertTrue(page._network_switches[2].isEnabled())

    def test_a_switch_that_worked_is_believed_because_the_router_said_so(self):
        page, scanner, _store = self.build()
        after = (
            Ssid(1, "Home 2.4G", "2.4GHz", enabled=True),
            Ssid(2, "Home kids", "2.4GHz", enabled=False),
            Ssid(6, "Guest", "5GHz", enabled=False),
        )
        page._network_switched(True, "Home kids is off the air.", list(after))
        self.assertFalse(page._network_switches[2].isChecked())
        self.assertEqual(scanner.scans, 1)


if __name__ == "__main__":

    unittest.main()


class WhatIsAskedOfTheRouterTests(PageTestCase):
    """The worker is built here but never run. What it was told is the point."""

    def test_turning_a_network_off_asks_for_that_network_by_number(self):
        page, _scanner, _store = self.build()
        self.answer = QMessageBox.Yes
        page._switch_network(2, False)
        self.assertEqual(page._network_worker.index, 2)
        self.assertFalse(page._network_worker.enabled)
        self.assertEqual(page._network_worker.label, "Home kids (2.4GHz)")

    def test_turning_one_on_asks_for_exactly_that(self):
        page, _scanner, _store = self.build()
        page._switch_network(6, True)
        self.assertEqual(page._network_worker.index, 6)
        self.assertTrue(page._network_worker.enabled)

    def test_the_switches_are_held_still_while_the_router_is_asked(self):
        page, _scanner, _store = self.build()
        self.answer = QMessageBox.Yes
        page._switch_network(2, False)
        self.assertTrue(page._busy_networks)
        self.assertFalse(any(s.isEnabled() for s in page._network_switches.values()))

    def test_blocking_asks_for_the_selected_device(self):
        page, _scanner, _store = self.build()
        self.answer = QMessageBox.Yes
        page._select("a2-27-ec-61-6a-a6")
        page._toggle_block()
        self.assertEqual(page._block_worker.mac, "a2-27-ec-61-6a-a6")
        self.assertTrue(page._block_worker.blocked)

    def test_a_blocked_device_is_asked_to_be_let_back_on(self):
        page, _scanner, _store = self.build(blocked=["a2-27-ec-61-6a-a6"])
        self.answer = QMessageBox.Yes
        page._select("a2-27-ec-61-6a-a6")
        page._toggle_block()
        self.assertFalse(page._block_worker.blocked)

    def test_answering_no_asks_the_router_for_nothing(self):
        page, _scanner, _store = self.build()
        self.answer = QMessageBox.No
        page._select("a2-27-ec-61-6a-a6")
        page._toggle_block()
        self.assertIsNone(getattr(page, "_block_worker", None))

    def test_the_button_says_it_is_working_while_it_works(self):
        page, _scanner, _store = self.build()
        self.answer = QMessageBox.Yes
        page._select("a2-27-ec-61-6a-a6")
        page._toggle_block()
        self.assertIn("Asking the router", page.block_button.text())
        self.assertFalse(page.block_button.isEnabled())

    def test_progress_from_the_worker_reaches_the_status_line(self):
        page, _scanner, _store = self.build()
        self.answer = QMessageBox.Yes
        page._select("a2-27-ec-61-6a-a6")
        page._toggle_block()
        page._block_worker.step.emit("Blocking on Home kids…")
        self.assertEqual(page.status_label.text(), "Blocking on Home kids…")


class ForgettingTests(PageTestCase):
    def test_forgetting_asks_first(self):
        page, scanner, _store = self.build()
        self.answer = QMessageBox.No
        page._select("a2-27-ec-61-6a-a6")
        page._forget()
        self.assertEqual(scanner.forgotten, [])

    def test_it_warns_that_a_name_someone_typed_goes_with_it(self):
        page, _scanner, _store = self.build()
        page._select("a2-27-ec-61-6a-a6")
        page._forget()
        self.assertIn("Shayan's phone", self.asked[-1][1])

    def test_confirmed_it_forgets_that_device(self):
        page, scanner, _store = self.build()
        self.answer = QMessageBox.Yes
        page._select("a2-27-ec-61-6a-a6")
        page._forget()
        self.assertEqual(scanner.forgotten, ["a2-27-ec-61-6a-a6"])


class BlockedAndNeverMetTests(unittest.TestCase):
    """A device the router refuses, that no scan can ever have met.

    Blocking is what stops it answering, so the list built from scans alone can
    never show it - and the page that shows it is the only place offering to let
    it back on. Someone blocked from the router's own pages, or from another PC,
    or before Mind was installed, is refused with nothing here saying why.
    """

    def test_the_router_naming_it_is_enough_for_a_row(self):
        from mind.network_scanner import with_the_blocked

        met = [phone("a2-27-ec-61-6a-a6", "192.168.18.12", "Shayan's phone")]
        devices = with_the_blocked(met, {"a2-27-ec-61-6a-a6", "00-08-22-33-1d-51"}, NOW)
        self.assertEqual(
            sorted(device.mac for device in devices),
            ["00-08-22-33-1d-51", "a2-27-ec-61-6a-a6"],
        )

    def test_a_device_already_on_the_list_is_not_added_twice(self):
        from mind.network_scanner import with_the_blocked

        met = [phone("a2-27-ec-61-6a-a6", "192.168.18.12", "Shayan's phone")]
        devices = with_the_blocked(met, {"a2-27-ec-61-6a-a6"}, NOW)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].custom_name, "Shayan's phone")

    def test_nothing_blocked_changes_nothing(self):
        from mind.network_scanner import with_the_blocked

        met = [phone("a2-27-ec-61-6a-a6", "192.168.18.12")]
        self.assertEqual(with_the_blocked(met, set(), NOW), met)

    def test_it_says_never_rather_than_counting_from_1970(self):
        from mind.network_scanner import with_the_blocked

        unmet = with_the_blocked([], {"00-08-22-33-1d-51"}, NOW)[0]
        self.assertEqual(unmet.seen_label(NOW), "never")
        self.assertFalse(unmet.online)

    def test_the_page_shows_it_as_blocked_and_offers_to_let_it_back_on(self):
        from mind.network_scanner import with_the_blocked

        page, _scanner, _store = self.build()
        page.blocked = {"00-08-22-33-1d-51"}
        page._show_devices(with_the_blocked([], page.blocked, NOW))
        page._select("00-08-22-33-1d-51")
        status = page.COLUMNS.index("Status")
        row = page.table.currentRow()
        self.assertEqual(page.table.item(row, status).text(), "Blocked")
        self.assertIn("back on", page.block_button.text())

    def build(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = ConfigStore(root=Path(temp.name) / "config")
        page = NetworkDevicesPage(store, None)
        self.addCleanup(page.deleteLater)
        return page, None, store

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
