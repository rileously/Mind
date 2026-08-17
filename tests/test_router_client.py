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
from mind.router_client import RouterError, RouterSession, normalise_mac, parse_devices


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

    def test_a_bare_address_is_given_a_scheme(self):
        self.assertEqual(RouterSession("192.168.18.1").base, "http://192.168.18.1")
        self.assertEqual(RouterSession("http://10.0.0.1/").base, "http://10.0.0.1")

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

if __name__ == "__main__":
    unittest.main()
