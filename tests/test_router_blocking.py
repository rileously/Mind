"""Keeping a device off the Wi-Fi, and letting it back on.

This is the part that writes to the router, so what matters is not only that a
block works but that a wrong one is refused. The router's own page warns that
changing the filter mode deletes every rule it has, and a whitelist means "only
these devices may connect" - so a router set that way is left alone rather than
switched.

The router here is a fake that answers the way the real one does: the same page
with the same token, the same three CGI endpoints, and a token that is spent by
the write that quotes it.
"""

import unittest
import urllib.parse

from mind.router_client import (
    FILTER_ADD,
    FILTER_DELETE,
    FILTER_PAGE,
    FILTER_SWITCH,
    WLAN_LIST_PAGE,
    BlockList,
    RouterError,
    RouterSession,
    parse_block_state,
    parse_ssids,
    router_mac,
)


WLAN_LIST = (
    'var WlanInfo = new Array('
    'new stWlanInfo("InternetGatewayDevice.LANDevice.1.WLANConfiguration.1","ath0","Home 2.4G","1","1","2.4GHz"),'
    'new stWlanInfo("InternetGatewayDevice.LANDevice.1.WLANConfiguration.2","ath1","Home kids","1","1","2.4GHz"),'
    'new stWlanInfo("InternetGatewayDevice.LANDevice.1.WLANConfiguration.5","ath4","Home 5G","1","1","5GHz"),'
    'new stWlanInfo("InternetGatewayDevice.LANDevice.1.WLANConfiguration.6","ath5","Guest","0","1","5GHz"));'
)


class FakeRouter(RouterSession):
    """Answers like the real firmware, and keeps what it is told."""

    def __init__(self, on: bool = False, blacklist: bool = True):
        super().__init__("192.168.18.1")
        self.on = on
        self.blacklist = blacklist
        self.rules: list[tuple[str, str, str]] = []  # domain, ssid, mac
        self.token = "token-0"
        self.writes: list[tuple[str, dict]] = []
        self.refused_stale_token = 0
        self.next_id = 1

    # -- what the router serves ------------------------------------------

    def page(self) -> str:
        rows = ",".join(
            f'new stMacFilter("{domain}","{ssid}","a name","{mac}")'
            for domain, ssid, mac in self.rules
        )
        return (
            "<html><script>"
            f"var enableFilter = '{1 if self.on else 0}';"
            f"var Mode = '{0 if self.blacklist else 1}';"
            f"var MacFilter = new Array({rows});"
            "</script>"
            f'<input type="hidden" name="onttoken" id="hwonttoken" value="{self.token}">'
            "</html>"
        )

    def read(self, path):
        if path == FILTER_PAGE:
            return 200, self.page()
        if path == WLAN_LIST_PAGE:
            return 200, WLAN_LIST
        return 404, "not found"

    def post(self, path, body, referer=""):
        fields = dict(urllib.parse.parse_qsl(body.decode(), keep_blank_values=True))
        self.writes.append((path, fields))
        if fields.get("x.X_HW_Token") != self.token:
            # The real firmware spends the token on the write that quotes it.
            self.refused_stale_token += 1
            return 403, "stale token"
        self.token = f"token-{len(self.writes)}"
        if path == FILTER_ADD:
            domain = f"InternetGatewayDevice.X_HW_Security.WLANMacFilter.{self.next_id}"
            self.next_id += 1
            self.rules.append(
                (domain, fields["x.SSIDName"], fields["x.SourceMACAddress"])
            )
        elif path == FILTER_DELETE:
            targets = {key for key in fields if key.startswith("InternetGatewayDevice")}
            self.rules = [rule for rule in self.rules if rule[0] not in targets]
        elif path == FILTER_SWITCH:
            self.on = fields["x.WlanMacFilterRight"] == "1"
        else:
            return 404, "no such form"
        return 200, "OK"


PHONE = "a2-27-ec-61-6a-a6"


class AddressTests(unittest.TestCase):
    def test_an_address_is_typed_the_way_the_form_wants_it(self):
        self.assertEqual(router_mac(PHONE), "A2:27:EC:61:6A:A6")
        self.assertEqual(router_mac("a2:27:ec:61:6a:a6"), "A2:27:EC:61:6A:A6")

    def test_something_that_is_not_an_address_is_refused_before_anything_is_sent(self):
        for written in ("", "192.168.1.5", "Adams-iPhone"):
            with self.assertRaises(RouterError):
                router_mac(written)


class ReadingTests(unittest.TestCase):
    def test_the_switch_the_mode_and_the_token_are_read_off_the_page(self):
        state = parse_block_state(FakeRouter(on=True).page())
        self.assertTrue(state.on)
        self.assertTrue(state.blacklist)
        self.assertEqual(state.token, "token-0")

    def test_a_whitelist_router_is_recognised_as_one(self):
        self.assertFalse(parse_block_state(FakeRouter(blacklist=False).page()).blacklist)

    def test_only_the_networks_that_are_switched_on_are_offered(self):
        networks = parse_ssids(WLAN_LIST)
        self.assertEqual([network.field for network in networks], ["SSID-1", "SSID-2", "SSID-5"])
        self.assertEqual(networks[0].name, "Home 2.4G")

    def test_a_page_that_is_not_the_filter_page_gives_nothing_rather_than_raising(self):
        state = parse_block_state("<html>a login screen</html>")
        self.assertEqual(state.entries, ())
        self.assertEqual(state.token, "")


class BlockingTests(unittest.TestCase):
    def test_a_device_is_blocked_on_every_network_the_router_broadcasts(self):
        # A rule on the 2.4 GHz network alone leaves the phone free to join the
        # 5 GHz one, which would read as a block that does not work.
        router = FakeRouter()
        BlockList(router).block(PHONE, "Adam's phone")
        self.assertEqual(
            sorted(ssid for _domain, ssid, _mac in router.rules),
            ["SSID-1", "SSID-2", "SSID-5"],
        )

    def test_the_filter_is_switched_on_when_it_was_not_already(self):
        # A list nobody enforces is not a block.
        router = FakeRouter(on=False)
        state = BlockList(router).block(PHONE)
        self.assertTrue(router.on)
        self.assertTrue(state.blocks(PHONE))

    def test_a_router_already_enforcing_its_list_is_not_switched_again(self):
        router = FakeRouter(on=True)
        BlockList(router).block(PHONE)
        self.assertNotIn(FILTER_SWITCH, [path for path, _fields in router.writes])

    def test_every_write_carries_the_token_from_the_page_it_just_read(self):
        router = FakeRouter()
        BlockList(router).block(PHONE)
        self.assertEqual(router.refused_stale_token, 0)

    def test_the_name_travels_with_the_rule_so_the_list_is_readable(self):
        router = FakeRouter()
        BlockList(router).block(PHONE, "Adam's phone")
        _path, fields = router.writes[0]
        self.assertEqual(fields["x.DeviceName"], "Adam's phone")
        self.assertEqual(fields["x.SourceMACAddress"], "A2:27:EC:61:6A:A6")

    def test_blocking_a_device_that_is_already_blocked_adds_nothing(self):
        router = FakeRouter()
        blocking = BlockList(router)
        blocking.block(PHONE)
        before = len(router.rules)
        blocking.block(PHONE)
        self.assertEqual(len(router.rules), before)

    def test_a_whitelist_router_is_refused_rather_than_switched(self):
        # Switching the mode deletes every rule the router has, and a whitelist
        # is what keeps that house on the Wi-Fi at all.
        router = FakeRouter(blacklist=False)
        with self.assertRaises(RouterError) as caught:
            BlockList(router).block(PHONE)
        self.assertIn("whitelist", str(caught.exception))
        self.assertEqual(router.writes, [])

    def test_a_page_without_a_token_stops_before_writing(self):
        router = FakeRouter()
        router.token = ""
        with self.assertRaises(RouterError):
            BlockList(router).block(PHONE)
        self.assertEqual(router.writes, [])


class UnblockingTests(unittest.TestCase):
    def test_a_blocked_device_is_let_back_on(self):
        router = FakeRouter()
        blocking = BlockList(router)
        blocking.block(PHONE)
        state = blocking.unblock(PHONE)
        self.assertEqual(router.rules, [])
        self.assertFalse(state.blocks(PHONE))

    def test_only_that_device_is_let_back_on(self):
        router = FakeRouter()
        blocking = BlockList(router)
        blocking.block(PHONE)
        blocking.block("b8-27-eb-11-22-33")
        blocking.unblock(PHONE)
        remaining = {mac for _domain, _ssid, mac in router.rules}
        self.assertEqual(remaining, {"B8:27:EB:11:22:33"})

    def test_unblocking_something_that_was_never_blocked_changes_nothing(self):
        router = FakeRouter()
        BlockList(router).unblock(PHONE)
        self.assertEqual(router.writes, [])

    def test_the_list_is_left_switched_on_so_other_blocks_still_hold(self):
        router = FakeRouter()
        blocking = BlockList(router)
        blocking.block(PHONE)
        blocking.block("b8-27-eb-11-22-33")
        blocking.unblock(PHONE)
        self.assertTrue(router.on)


class StateTests(unittest.TestCase):
    def test_a_rule_on_a_filter_that_is_off_is_not_a_block(self):
        router = FakeRouter()
        BlockList(router).block(PHONE)
        router.on = False
        self.assertFalse(BlockList(router).state().blocks(PHONE))
        self.assertEqual(BlockList(router).state().blocked_macs, ())

    def test_who_is_blocked_comes_back_in_the_form_the_rest_of_mind_uses(self):
        router = FakeRouter()
        BlockList(router).block(PHONE)
        self.assertEqual(BlockList(router).state().blocked_macs, (PHONE,))


class PageTests(unittest.TestCase):
    """What the Wi-Fi devices page offers, and what it refuses to offer."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import tempfile
        from pathlib import Path

        from mind.config_store import ConfigStore
        from mind.main_window import NetworkDevicesPage
        from mind.network_devices import Device

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.page = NetworkDevicesPage(self.store, None)
        self.addCleanup(self.page.deleteLater)
        self.phone = Device(mac=PHONE, ip="192.168.18.12", hostname="Adams phone", online=True)

    def show(self, devices, blocked=()):
        self.page.blocked = {*blocked}
        self.page._show_devices(list(devices))
        self.page.table.setCurrentCell(0, 0)

    def test_a_blocked_device_reads_as_blocked_rather_than_online(self):
        # It may well still be there and still trying, so "Online" would read
        # as though the block had not worked.
        self.show([self.phone], blocked=[PHONE])
        self.assertEqual(self.page.table.item(0, 4).text(), "Blocked")

    def test_the_button_offers_the_way_the_click_would_go(self):
        self.show([self.phone])
        self.assertEqual(self.page.block_button.text(), "Block")
        self.show([self.phone], blocked=[PHONE])
        self.assertEqual(self.page.block_button.text(), "Unblock")

    def test_this_pc_is_never_offered_for_blocking(self):
        # Blocking it over Wi-Fi would cut the connection that undoes it.
        from mind.network_devices import Device, local_ipv4

        here = local_ipv4()
        if not here:
            self.skipTest("this machine has no address on a network")
        self.show([Device(mac="b8-27-eb-11-22-33", ip=here, hostname="this pc")])
        self.assertFalse(self.page.block_button.isEnabled())

    def test_nothing_selected_means_nothing_to_block(self):
        self.page.blocked = set()
        self.page._show_devices([])
        self.assertFalse(self.page.block_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
