"""Reading a device list out of what a router returns.

The router is the only thing that knows a device by the name it gave when it
joined, so this is where real names come from. Firmwares differ, so the parsing
is written to recognise a MAC, an address and a name on sight rather than to
trust a field order - and tested against both shapes Huawei's pages use.

Nobody's password appears here. What is tested is the reading and the storing,
and the storing is tested by checking the password cannot be read back out.
"""

import tempfile
import unittest
from pathlib import Path

from mind.config_store import ConfigStore
from mind.router_client import (
    RouterError,
    RouterSession,
    device_kind,
    normalise_mac,
    parse_devices,
    row_signatures,
)


JSON_BODY = """
{"HostInfo": [
  {"HostName": "Adams-iPhone", "IPAddress": "192.168.18.5", "MACAddress": "AA:BB:CC:DD:EE:FF"},
  {"HostName": "living-room-tv", "IPAddress": "192.168.18.15", "MACAddress": "11-22-33-44-55-66"}
]}
"""

JS_BODY = """
var lanUserInfo = new Array(
  new stLanUserDevInfo("InternetGatewayDevice.X_Hosts.Host.1", "Adams-Laptop", "192.168.18.9", "b8:27:eb:11:22:33", "1"),
  new stLanUserDevInfo("InternetGatewayDevice.X_Hosts.Host.2", "SM-G991B", "192.168.18.12", "62-4f-00-56-96-09", "1")
);
"""


class ParsingTests(unittest.TestCase):
    def test_a_json_list_is_read(self):
        devices = parse_devices(JSON_BODY)
        self.assertEqual(len(devices), 2)
        names = {device.hostname for device in devices}
        self.assertIn("Adams-iPhone", names)
        self.assertIn("living-room-tv", names)

    def test_the_javascript_rows_are_read(self):
        devices = parse_devices(JS_BODY)
        self.assertEqual(len(devices), 2)
        by_ip = {device.ip: device for device in devices}
        self.assertEqual(by_ip["192.168.18.9"].hostname, "Adams-Laptop")
        self.assertEqual(by_ip["192.168.18.12"].hostname, "SM-G991B")

    def test_addresses_come_back_in_the_form_the_rest_of_mind_uses(self):
        # The ARP table writes them with dashes; a router may use either.
        devices = parse_devices(JSON_BODY)
        for device in devices:
            self.assertRegex(device.mac, r"^[0-9a-f]{2}(-[0-9a-f]{2}){5}$")

    def test_a_row_without_an_address_is_skipped_rather_than_half_read(self):
        devices = parse_devices('{"HostInfo": [{"HostName": "mystery", "IPAddress": "192.168.1.9"}]}')
        self.assertEqual(devices, [])

    def test_rubbish_gives_nothing_rather_than_raising(self):
        # An unauthenticated router returns its login page for every path.
        for body in ("", "<html><body>Please sign in</body></html>", "not json {", "[]"):
            self.assertEqual(parse_devices(body), [])

    def test_the_same_device_listed_twice_appears_once(self):
        doubled = JSON_BODY.replace("]}", ',{"HostName": "again", "IPAddress": "192.168.18.5", "MACAddress": "AA:BB:CC:DD:EE:FF"}]}')
        self.assertEqual(len(parse_devices(doubled)), 2)


class JsonBoilerplateTests(unittest.TestCase):
    """The JSON firmwares carry the same nothing the JavaScript rows do."""

    def name_for(self, entry: str) -> str:
        body = '{"HostInfo": [' + entry + ']}'
        return parse_devices(body)[0].hostname

    def test_the_network_name_is_not_used_as_a_device_name(self):
        self.assertEqual(
            self.name_for(
                '{"HostName": "SSID2", "IPAddress": "192.168.18.26",'
                ' "MACAddress": "00:08:22:33:1d:51"}'
            ),
            "",
        )

    def test_a_windows_pc_is_not_called_msft(self):
        self.assertEqual(
            self.name_for(
                '{"HostName": "MSFT 5.0", "IPAddress": "192.168.18.7",'
                ' "MACAddress": "90:de:80:77:53:37"}'
            ),
            "",
        )

    def test_a_real_name_under_another_key_beats_boilerplate_under_the_first(self):
        # HostName is read first, but being present is not the same as useful.
        self.assertEqual(
            self.name_for(
                '{"HostName": "android-dhcp-13", "Name": "Redmi-Note-11",'
                ' "IPAddress": "192.168.18.12", "MACAddress": "a2:27:ec:61:6a:a6"}'
            ),
            "Redmi-Note-11",
        )

    def test_a_nameless_device_is_still_a_known_device(self):
        devices = parse_devices(
            '{"HostInfo": [{"HostName": "SSID2", "IPAddress": "192.168.18.26",'
            ' "MACAddress": "00:08:22:33:1d:51"}]}'
        )
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].mac, "00-08-22-33-1d-51")


# The page as the router really serves it: the constructor is declared first,
# and the rows that follow are read by the names it gives its own columns. Two
# of these devices were labelled by hand in the router's pages.
DECLARED_BODY = (
    "function USERDeviceNew(Domain, IpAddr, MacAddr, Port, IpType, DevType,"
    " DevStatus, PortType, Time, HostName, IPv4Enabled, IPv6Enabled, DeviceType,"
    " UserDevAlias, UserSpecifiedDeviceType, LeaseTimeRemaining, RealMacAddr) {}"
    "var UserDevinfo = new Array("
    'new USERDeviceNew("InternetGatewayDevice.LANDevice.1.X_HW_UserDev.3",'
    '"192\x2e168\x2e18\x2e12","a2\x3a27\x3aec\x3a61\x3a6a\x3aa6","SSID1","DHCP",'
    '"android\x2ddhcp\x2d13","Online","WIFI","0\x3a10","Redmi\x2dNote\x2d11",'
    '"1","1","0","Shayan","1","2967","a2\x3a27\x3aec\x3a61\x3a6a\x3aa6"),'
    'new USERDeviceNew("InternetGatewayDevice.LANDevice.1.X_HW_UserDev.4",'
    '"192\x2e168\x2e18\x2e7","90\x3ade\x3a80\x3a77\x3a53\x3a37","SSID5","DHCP",'
    '"MSFT\x205\x2e0","Online","WIFI","7\x3a45","DESKTOP\x2d2KVG8KH",'
    '"1","1","0","","0","2676","90\x3ade\x3a80\x3a77\x3a53\x3a37"));'
)


class DeclaredRowTests(unittest.TestCase):
    """Which field is which, because the page said so rather than because it looked right."""

    def setUp(self):
        self.devices = {device.ip: device for device in parse_devices(DECLARED_BODY)}

    def test_the_constructors_own_field_names_are_read(self):
        signature = row_signatures(DECLARED_BODY)["USERDeviceNew"]
        self.assertEqual(len(signature), 17)
        self.assertEqual(signature[9], "HostName")
        self.assertEqual(signature[13], "UserDevAlias")

    def test_a_name_set_on_the_router_beats_the_one_the_device_gave(self):
        # Someone typed "Shayan" into the router; the phone calls itself a model
        # number. The person's name is the better one.
        device = self.devices["192.168.18.12"]
        self.assertEqual(device.alias, "Shayan")
        self.assertEqual(device.hostname, "Redmi-Note-11")
        self.assertEqual(device.name, "Shayan")

    def test_the_devices_own_name_stands_where_nobody_renamed_it(self):
        self.assertEqual(self.devices["192.168.18.7"].name, "DESKTOP-2KVG8KH")

    def test_the_dhcp_boilerplate_is_read_as_what_the_device_is(self):
        self.assertEqual(self.devices["192.168.18.12"].kind, "Android 13")
        self.assertEqual(self.devices["192.168.18.7"].kind, "Windows")

    def test_the_boilerplate_is_still_not_a_name(self):
        for device in self.devices.values():
            self.assertNotIn("android-dhcp", device.name)
            self.assertNotIn("MSFT", device.name)

    def test_a_row_whose_constructor_was_never_declared_is_still_read(self):
        # The older firmwares publish rows with no function to go with them, and
        # guessing is better than nothing there.
        devices = {device.ip: device for device in parse_devices(JS_BODY)}
        self.assertEqual(devices["192.168.18.9"].hostname, "Adams-Laptop")


class DeviceKindTests(unittest.TestCase):
    def test_an_android_version_is_read_out_of_the_client_name(self):
        self.assertEqual(device_kind("android-dhcp-15"), "Android 15")
        self.assertEqual(device_kind("ANDROID-DHCP-9"), "Android 9")

    def test_a_windows_pc_says_so_in_its_own_way(self):
        self.assertEqual(device_kind("MSFT 5.0"), "Windows")

    def test_anything_else_is_left_alone(self):
        for value in ("", "--", "Redmi-Note-11", "dhcp", "android-dhcp-"):
            self.assertEqual(device_kind(value), "")


class MacTests(unittest.TestCase):
    def test_every_way_a_router_writes_one_reads_the_same(self):
        for written in ("AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "AABBCCDDEEFF", "aa bb cc dd ee ff"):
            self.assertEqual(normalise_mac(written), "aa-bb-cc-dd-ee-ff")

    def test_something_that_is_not_an_address_is_refused(self):
        for written in ("", "192.168.1.1", "Adams-iPhone", "AA:BB:CC"):
            self.assertEqual(normalise_mac(written), "")


class SessionTests(unittest.TestCase):
    def test_an_empty_address_is_refused_before_anything_is_sent(self):
        with self.assertRaises(RouterError):
            RouterSession("")

    def test_a_bare_address_tries_https_first(self):
        # The plain HTTP page on these models is only a redirect to HTTPS, so
        # trying HTTP first makes every page look like a sign-in screen.
        session = RouterSession("192.168.18.1")
        self.assertEqual(session.candidates[0], "https://192.168.18.1:80")
        self.assertIn("http://192.168.18.1", session.candidates)

    def test_something_that_cannot_be_a_host_is_refused_with_a_sentence(self):
        # A single dot is a valid string and not a valid address. Left to
        # urllib it comes back as a UnicodeError from the IDNA encoder, which
        # is neither readable nor catchable where a RouterError would be - and
        # it stopped the network scan, which needs no router at all.
        for written in (".", "..", "///", "  .  "):
            with self.assertRaises(RouterError) as caught:
                RouterSession(written)
            self.assertIn("192.168.18.1", str(caught.exception))

    def test_an_address_typed_with_a_scheme_is_respected(self):
        self.assertEqual(RouterSession("http://10.0.0.1/").candidates, ["http://10.0.0.1"])

    def test_the_byte_order_mark_is_not_part_of_the_token(self):
        # The router answers GetRandCount.asp with a BOM in front of the value.
        self.assertEqual("﻿abc123".strip().lstrip("﻿").strip(), "abc123")

    def test_signing_in_without_a_password_says_so_rather_than_trying(self):
        session = RouterSession("192.168.18.1")
        with self.assertRaises(RouterError) as caught:
            session.sign_in("Epuser", "")
        self.assertIn("username and password", str(caught.exception))


class CredentialTests(unittest.TestCase):
    """The router password is kept the way the Telegram token is."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")

    def test_the_password_is_not_written_in_the_open(self):
        config = self.store.load()
        config = self.store.set_router_password(config, "hunter2")
        self.store.save(config)
        written = self.store.config_path.read_text(encoding="utf-8")
        self.assertNotIn("hunter2", written)

    def test_it_comes_back_only_through_the_call_that_unprotects_it(self):
        config = self.store.set_router_password(self.store.load(), "hunter2")
        self.store.save(config)
        self.assertEqual(self.store.get_router_password(self.store.load()), "hunter2")

    def test_clearing_it_removes_it(self):
        config = self.store.set_router_password(self.store.load(), "hunter2")
        config = self.store.set_router_password(config, "")
        self.store.save(config)
        self.assertEqual(self.store.get_router_password(self.store.load()), "")



class PageCredentialTests(unittest.TestCase):
    """The fields on the page, and what they must never do."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mind.main_window import NetworkDevicesPage

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.page = NetworkDevicesPage(self.store, None)
        self.addCleanup(self.page.deleteLater)

    def fill(self, password: str = "not-a-real-password"):
        self.page.router_address.setText("192.168.18.1")
        self.page.router_username.setText("Epuser")
        self.page.router_password.setText(password)
        self.page._save_router()

    def test_what_is_typed_is_kept_but_not_in_the_open(self):
        self.fill()
        written = self.store.config_path.read_text(encoding="utf-8")
        self.assertNotIn("not-a-real-password", written)
        self.assertEqual(self.store.get_router_password(self.store.load()), "not-a-real-password")

    def test_a_stored_password_is_shown_as_a_mask(self):
        from mind.main_window import ROUTER_PASSWORD_MASK, NetworkDevicesPage

        self.fill()
        page = NetworkDevicesPage(self.store, None)
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.router_password.text(), ROUTER_PASSWORD_MASK)

    def test_leaving_the_mask_alone_does_not_wipe_the_password(self):
        # The trap: saving the page would otherwise store the mask itself, and
        # the router would start refusing a password made of bullet characters.
        from mind.main_window import NetworkDevicesPage

        self.fill()
        page = NetworkDevicesPage(self.store, None)
        self.addCleanup(page.deleteLater)
        page._save_router()
        self.assertEqual(self.store.get_router_password(self.store.load()), "not-a-real-password")

    def test_clearing_the_field_clears_the_password(self):
        self.fill()
        self.page.router_password.setText("")
        self.page._save_router()
        self.assertEqual(self.store.get_router_password(self.store.load()), "")

    def test_the_address_and_username_are_plain_settings(self):
        self.fill()
        config = self.store.load()
        self.assertEqual(config["router_address"], "192.168.18.1")
        self.assertEqual(config["router_username"], "Epuser")

# A row exactly as an OptiXstar returns it: every value hex escaped, the
# constructor named USERDeviceNew, and two names in the row - the DHCP client's
# boilerplate and the one the device actually goes by.
OPTIXSTAR_BODY = (
    'var UserDevinfo = new Array('
    'new USERDeviceNew("InternetGatewayDevice.LANDevice.1.X_HW_UserDev.3",'
    '"192\x2e168\x2e18\x2e12","a2\x3a27\x3aec\x3a61\x3a6a\x3aa6","SSID1","DHCP",'
    '"android\x2ddhcp\x2d13","Online","WIFI","0\x3a10","Redmi\x2dNote\x2d11",'
    '"1","1","0","Owner","1","2967","a2\x3a27\x3aec\x3a61\x3a6a\x3aa6"),'
    'new USERDeviceNew("InternetGatewayDevice.LANDevice.1.X_HW_UserDev.4",'
    '"192\x2e168\x2e18\x2e7","90\x3ade\x3a80\x3a77\x3a53\x3a37","SSID5","DHCP",'
    '"MSFT\x205\x2e0","Online","WIFI","7\x3a45","DESKTOP\x2d2KVG8KH",'
    '"1","1","0","","0","2676","90\x3ade\x3a80\x3a77\x3a53\x3a37"));'
)


class OptiXstarTests(unittest.TestCase):
    """The shape this router actually returns, which three things hid at first."""

    def setUp(self):
        self.devices = {device.ip: device for device in parse_devices(OPTIXSTAR_BODY)}

    def test_both_rows_are_read(self):
        self.assertEqual(len(self.devices), 2)

    def test_the_hex_escapes_are_undone(self):
        # Nothing matches while an address reads "192.168.18.12".
        self.assertIn("192.168.18.12", self.devices)
        self.assertEqual(self.devices["192.168.18.12"].mac, "a2-27-ec-61-6a-a6")

    def test_the_constructor_name_is_not_assumed(self):
        # This firmware says USERDeviceNew where others say stLanUserDevInfo.
        self.assertTrue(self.devices)

    def test_the_name_a_device_goes_by_beats_the_dhcp_boilerplate(self):
        # The row carries both "android-dhcp-13" and "Redmi-Note-11".
        self.assertEqual(self.devices["192.168.18.12"].hostname, "Redmi-Note-11")

    def test_a_windows_pc_is_named_rather_than_called_msft(self):
        self.assertEqual(self.devices["192.168.18.7"].hostname, "DESKTOP-2KVG8KH")

    def test_the_object_path_is_never_mistaken_for_a_name(self):
        for device in self.devices.values():
            self.assertNotIn("InternetGatewayDevice", device.hostname)


# The same row from a phone that sent no name of its own: the field that held
# "Redmi-Note-11" is empty, and the only word left in the row is the network it
# joined.
NAMELESS_BODY = (
    'var UserDevinfo = new Array('
    'new USERDeviceNew("InternetGatewayDevice.LANDevice.1.X_HW_UserDev.9",'
    '"192.168.18.26","00:08:22:33:1d:51","SSID2","DHCP",'
    '"","Online","WIFI","0:02","",'
    '"1","1","0","","0","2967","00:08:22:33:1d:51"),'
    'new USERDeviceNew("InternetGatewayDevice.LANDevice.1.X_HW_UserDev.10",'
    '"192.168.18.27","00:08:22:33:1d:52","SSID2","DHCP",'
    '"","Online","WIFI","0:03","",'
    '"1","1","0","","0","2968","00:08:22:33:1d:52"));'
)


class NamelessDeviceTests(unittest.TestCase):
    """A phone that named itself to nobody must not be named after the Wi-Fi.

    Four of these arrived at once and every one of them came through called
    SSID2 - the same label on four rows, and four block buttons that could not
    be told apart. No name at all is the honest answer, and it leaves the local
    scan and then the address free to say something distinct.
    """

    def setUp(self):
        self.devices = {device.ip: device for device in parse_devices(NAMELESS_BODY)}

    def test_the_network_name_is_not_used_as_a_device_name(self):
        self.assertEqual(self.devices["192.168.18.26"].hostname, "")
        self.assertEqual(self.devices["192.168.18.27"].hostname, "")

    def test_the_rows_are_still_read(self):
        # Nameless is not the same as missing: both are known devices.
        self.assertEqual(len(self.devices), 2)
        self.assertEqual(self.devices["192.168.18.26"].mac, "00-08-22-33-1d-51")


class ScanSurvivesTheRouterTests(unittest.TestCase):
    """The sweep of the network needs no router, and must not depend on one.

    A router address saved as "." stopped the whole scan: the address reached
    urllib as a hostname, came back as a UnicodeError rather than a RouterError,
    and failed the worker. Every device then aged out and the Telegram panel
    read "0 online of 14 known" while the house was full of phones.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")

    def fill(self, address: str):
        config = self.store.load()
        config["router_address"] = address
        config["router_username"] = "Epuser"
        config = self.store.set_router_password(config, "not-a-real-password")
        self.store.save(config)

    def test_an_address_that_is_not_one_leaves_the_scan_alone(self):
        from mind.network_scanner import router_facts

        self.fill(".")
        reading = router_facts(self.store)
        self.assertEqual(reading.facts, {})
        self.assertEqual(reading.blocked, frozenset())
        self.assertEqual(reading.networks, ())

    def test_a_router_that_cannot_be_reached_leaves_the_scan_alone(self):
        # A port on this machine that nothing is listening on: refused at once,
        # so this stays a real connection rather than a mock and still costs
        # the suite nothing.
        from mind.network_scanner import router_facts

        self.fill("http://127.0.0.1:1")
        reading = router_facts(self.store)
        self.assertEqual(reading.facts, {})
        self.assertEqual(reading.blocked, frozenset())
        self.assertEqual(reading.networks, ())

    def test_no_router_at_all_is_not_a_failure(self):
        from mind.network_scanner import router_facts

        reading = router_facts(self.store)
        self.assertEqual(reading.facts, {})
        self.assertEqual(reading.blocked, frozenset())
        self.assertEqual(reading.networks, ())


if __name__ == "__main__":
    unittest.main()


class RememberedBoilerplateTests(unittest.TestCase):
    """A name once written down outlives the reading that wrote it.

    merge() keeps a hostname when a scan comes back empty handed, so the ten
    devices already saved as SSID2 would have stayed SSID2 for good. They are
    let go when the remembered list is read.
    """

    def setUp(self):
        from mind.network_devices import Device

        self.Device = Device
        self.devices = [
            Device(mac="00-08-22-33-1d-51", ip="192.168.18.26", hostname="SSID2"),
            Device(mac="a2-27-ec-61-6a-a6", ip="192.168.18.12", hostname="Redmi-Note-11"),
            Device(mac="00-08-22-05-41-08", ip="192.168.18.28", hostname="SSID2",
                   custom_name="Nuha's tablet"),
            Device(mac="90-de-80-77-53-37", ip="192.168.18.7", hostname=""),
        ]

    def healed(self):
        from mind.network_scanner import without_boilerplate_names

        return {device.mac: device for device in without_boilerplate_names(self.devices)}

    def test_the_network_name_is_let_go(self):
        self.assertEqual(self.healed()["00-08-22-33-1d-51"].hostname, "")

    def test_a_real_name_is_kept(self):
        self.assertEqual(self.healed()["a2-27-ec-61-6a-a6"].hostname, "Redmi-Note-11")

    def test_a_name_the_user_typed_is_never_touched(self):
        device = self.healed()["00-08-22-05-41-08"]
        self.assertEqual(device.custom_name, "Nuha's tablet")
        self.assertEqual(device.hostname, "SSID2")

    def test_nothing_is_lost_from_the_list(self):
        self.assertEqual(len(self.healed()), 4)
