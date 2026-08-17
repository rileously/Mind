"""Remembering the network between scans.

Scanning is network work and is verified against a real network; what is tested
here is the part with the awkward questions. When is a device new? What happens
when the router gives it a different address, or when a scan comes back without
the name a previous scan found? What must never be overwritten?
"""

import tempfile
import time
import unittest
from pathlib import Path

from mind.config_store import ConfigStore
from mind.network_devices import (
    UNKNOWN,
    Device,
    Observation,
    from_dict,
    is_randomised,
    merge,
    rename,
    subnet_addresses,
    to_dict,
    vendor_for,
)


NOW = 1_700_000_000.0


class MergeTests(unittest.TestCase):
    def test_everything_is_new_the_first_time(self):
        devices, arrivals = merge([], [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5")], NOW)
        self.assertEqual(len(arrivals), 1)
        self.assertTrue(devices[0].online)
        self.assertEqual(devices[0].first_seen, NOW)

    def test_a_device_seen_again_is_not_new(self):
        first, _ = merge([], [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5")], NOW)
        _, arrivals = merge(first, [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5")], NOW + 60)
        self.assertEqual(arrivals, [])

    def test_a_new_address_is_the_same_device(self):
        # Routers hand out different addresses; a device is its MAC.
        first, _ = merge([], [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5")], NOW)
        devices, arrivals = merge(
            first, [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.99")], NOW + 3600
        )
        self.assertEqual(arrivals, [])
        self.assertEqual(devices[0].ip, "192.168.1.99")
        self.assertEqual(devices[0].first_seen, NOW, "it was first seen when it was")

    def test_a_device_that_stops_answering_goes_offline_but_is_remembered(self):
        first, _ = merge([], [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5")], NOW)
        devices, _ = merge(first, [], NOW + 600)
        self.assertEqual(len(devices), 1)
        self.assertFalse(devices[0].online)
        self.assertEqual(devices[0].last_seen, NOW)

    def test_a_phone_that_misses_one_scan_is_not_marked_gone_at_once(self):
        # Phones sleep between beacons; flickering between here and gone every
        # minute would make the list unreadable.
        first, _ = merge([], [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5")], NOW)
        devices, _ = merge(first, [], NOW + 60, online_grace=210)
        self.assertTrue(devices[0].online)
        devices, _ = merge(devices, [], NOW + 400, online_grace=210)
        self.assertFalse(devices[0].online)

    def test_a_name_already_found_is_not_lost_when_a_scan_is_quiet(self):
        # mDNS often answers once and then says nothing for several scans.
        first, _ = merge(
            [], [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5", "Adams-iPhone")], NOW
        )
        devices, _ = merge(first, [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5")], NOW + 60)
        self.assertEqual(devices[0].hostname, "Adams-iPhone")

    def test_a_custom_name_is_never_overwritten_by_discovery(self):
        first, _ = merge([], [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5", "android-1234")], NOW)
        named = rename(first, "aa-bb-cc-dd-ee-ff", "Adam's Phone")
        devices, _ = merge(named, [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5", "android-9999")], NOW + 60)
        self.assertEqual(devices[0].custom_name, "Adam's Phone")
        self.assertEqual(devices[0].display_name, "Adam's Phone")
        self.assertEqual(devices[0].hostname, "android-9999", "discovery still updates underneath")

    def test_the_online_ones_are_listed_first(self):
        known, _ = merge(
            [],
            [Observation("11-11-11-11-11-11", "192.168.1.2"), Observation("22-22-22-22-22-22", "192.168.1.3")],
            NOW,
        )
        devices, _ = merge(known, [Observation("22-22-22-22-22-22", "192.168.1.3")], NOW + 600)
        self.assertTrue(devices[0].online)
        self.assertFalse(devices[-1].online)


class NamingTests(unittest.TestCase):
    def test_a_device_with_nothing_known_reads_as_unknown(self):
        self.assertEqual(Device(mac="aa-bb-cc-dd-ee-ff").display_name, UNKNOWN)

    def test_the_best_name_available_is_used(self):
        device = Device(mac="aa", vendor="Apple")
        self.assertEqual(device.display_name, "Apple")
        device = Device(mac="aa", vendor="Apple", hostname="Adams-iPhone")
        self.assertEqual(device.display_name, "Adams-iPhone")
        device = Device(mac="aa", vendor="Apple", hostname="Adams-iPhone", custom_name="Phone")
        self.assertEqual(device.display_name, "Phone")

    def test_a_randomised_address_says_so_rather_than_unknown(self):
        # Phones invent a MAC per network, so there is no maker to look up and
        # "Unknown" would suggest something had failed.
        self.assertTrue(is_randomised("62-4f-00-56-96-09"))
        self.assertEqual(vendor_for("62-4f-00-56-96-09"), "Randomised")
        self.assertFalse(is_randomised("b4-61-42-5b-ec-90"))

    def test_a_known_maker_is_named(self):
        self.assertEqual(vendor_for("b8-27-eb-11-22-33"), "Raspberry Pi")
        self.assertEqual(vendor_for("B8-27-EB-11-22-33"), "Raspberry Pi")

    def test_an_unknown_fixed_address_stays_empty(self):
        self.assertEqual(vendor_for("80-00-4b-40-70-6c"), "")

    def test_last_seen_reads_as_time_passed(self):
        device = Device(mac="aa", last_seen=NOW, online=True)
        self.assertEqual(device.seen_label(NOW), "now")
        offline = Device(mac="aa", last_seen=NOW)
        self.assertEqual(offline.seen_label(NOW + 300), "5 min ago")
        self.assertEqual(offline.seen_label(NOW + 7200), "2 hours ago")
        self.assertEqual(offline.seen_label(NOW + 90000), "1 day ago")


class StorageTests(unittest.TestCase):
    def test_a_device_survives_a_round_trip(self):
        device = Device(
            mac="aa-bb-cc-dd-ee-ff",
            ip="192.168.1.5",
            hostname="pi",
            vendor="Raspberry Pi",
            custom_name="Doorbell",
            first_seen=NOW,
            last_seen=NOW + 5,
        )
        restored = from_dict(to_dict(device))
        self.assertEqual(restored.custom_name, "Doorbell")
        self.assertEqual(restored.first_seen, NOW)

    def test_nothing_is_online_until_a_scan_says_so(self):
        # A saved file is history, not a report on the network right now.
        restored = from_dict({"mac": "aa", "ip": "1.2.3.4"})
        self.assertFalse(restored.online)

    def test_rubbish_is_dropped_rather_than_guessed_at(self):
        self.assertIsNone(from_dict({"ip": "1.2.3.4"}))
        self.assertIsNone(from_dict("not a device"))

    def test_the_store_keeps_them_between_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConfigStore(root=Path(folder) / "config")
            devices, _ = merge([], [Observation("aa-bb-cc-dd-ee-ff", "192.168.1.5")], NOW)
            store.save_devices([to_dict(device) for device in devices])
            reloaded = [from_dict(item) for item in store.load_devices()]
            self.assertEqual(reloaded[0].mac, "aa-bb-cc-dd-ee-ff")


class SubnetTests(unittest.TestCase):
    def test_the_sweep_covers_the_subnet_but_not_this_pc(self):
        addresses = subnet_addresses("192.168.18.7")
        self.assertEqual(len(addresses), 253)
        self.assertIn("192.168.18.1", addresses)
        self.assertIn("192.168.18.254", addresses)
        self.assertNotIn("192.168.18.7", addresses)
        self.assertNotIn("192.168.18.255", addresses)

    def test_nothing_is_swept_without_an_address(self):
        self.assertEqual(subnet_addresses(""), [])
        self.assertEqual(subnet_addresses("nonsense"), [])


class DevicesPanelTests(unittest.TestCase):
    """The /devices panel reads what the scanner stored; it never scans itself."""

    def setUp(self):
        from mind.telegram_bridge import TelegramBridge
        from tests.test_telegram_menu_flow import FakeClient

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.bridge = TelegramBridge(self.store)
        self.client = FakeClient()
        self.config = {"network_scan_enabled": True}

    def save(self, *devices):
        self.store.save_devices([to_dict(device) for device in devices])

    def test_it_says_so_when_scanning_is_off(self):
        self.bridge._send_devices_panel(self.client, 7, {})
        self.assertIn("switched off", self.client.sent[-1]["text"].replace("scanning is off", "switched off"))

    def test_it_lists_what_was_found(self):
        self.save(
            Device(mac="aa-bb-cc-dd-ee-ff", ip="192.168.1.5", custom_name="Adam's Phone", last_seen=time.time()),
            Device(mac="11-22-33-44-55-66", ip="192.168.1.9", vendor="Raspberry Pi", last_seen=time.time()),
        )
        self.bridge._send_devices_panel(self.client, 7, self.config)
        text = self.client.sent[-1]["text"]
        self.assertIn("Adam's Phone", text)
        self.assertIn("Raspberry Pi", text)
        self.assertIn("2 online", text)

    def test_a_device_not_seen_recently_is_listed_as_gone(self):
        # The file is history; whether something is here now is decided by when
        # it was last seen, not by what was saved.
        self.save(Device(mac="aa-bb-cc-dd-ee-ff", ip="192.168.1.5", last_seen=time.time() - 7200))
        self.bridge._send_devices_panel(self.client, 7, self.config)
        text = self.client.sent[-1]["text"]
        self.assertIn("0 online", text)
        self.assertIn("Seen before", text)

    def test_nothing_found_yet_reads_plainly(self):
        self.bridge._send_devices_panel(self.client, 7, self.config)
        self.assertIn("Nothing found", self.client.sent[-1]["text"])

    def test_asking_again_replaces_the_panel(self):
        self.save(Device(mac="aa", ip="1.2.3.4", last_seen=time.time()))
        self.bridge._send_devices_panel(self.client, 7, self.config)
        first = self.client.sent[-1]["id"]
        self.bridge._send_devices_panel(self.client, 7, self.config)
        self.assertEqual(self.client.deleted, [first])


if __name__ == "__main__":
    unittest.main()
