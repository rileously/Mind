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
    WIRED_ADD,
    WIRED_DELETE,
    WIRED_PAGE,
    WIRED_SWITCH,
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
        self.refused_field_order = 0
        self.next_id = 1
        # Some firmwares write the network back the way the device rows spell
        # it, without the hyphen the form was given.
        self.echo_ssid_without_the_hyphen = False
        # How many wireless rules this list will take before it is full.
        self.refuse_wifi_after = None

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
        pairs = urllib.parse.parse_qsl(body.decode(), keep_blank_values=True)
        fields = dict(pairs)
        self.writes.append((path, fields))
        if pairs and pairs[-1][0] != "x.X_HW_Token":
            # What the router really does, and the reason a working block
            # stopped working: the same fields are accepted in one order and
            # refused in another. Anything after the token is a 403.
            self.refused_field_order += 1
            return 403, "the token must come last"
        if fields.get("x.X_HW_Token") != self.token:
            # The real firmware spends the token on the write that quotes it.
            self.refused_stale_token += 1
            return 403, "stale token"
        self.token = f"token-{len(self.writes)}"
        if path == FILTER_ADD:
            if self.refuse_wifi_after is not None and len(self.rules) >= self.refuse_wifi_after:
                return 403, "that list is full"
            domain = f"InternetGatewayDevice.X_HW_Security.WLANMacFilter.{self.next_id}"
            self.next_id += 1
            ssid = fields["x.SSIDName"]
            if self.echo_ssid_without_the_hyphen:
                ssid = ssid.replace("-", "")
            self.rules.append((domain, ssid, fields["x.SourceMACAddress"]))
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

    def test_every_network_is_offered_including_the_ones_switched_off(self):
        # The Guest network is off. It is still listed, because it is one click
        # from being on again and that click is now Mind's to make - a device
        # blocked on the other three and not on this one would walk straight
        # back on the moment somebody raised it.
        networks = parse_ssids(WLAN_LIST)
        self.assertEqual(
            [network.field for network in networks],
            ["SSID-1", "SSID-2", "SSID-5", "SSID-6"],
        )
        self.assertEqual(networks[0].name, "Home 2.4G")

    def test_which_of_them_are_on_the_air_is_read_too(self):
        on_air = {network.field: network.enabled for network in parse_ssids(WLAN_LIST)}
        self.assertTrue(on_air["SSID-2"])
        self.assertFalse(on_air["SSID-6"])

    def test_where_the_router_keeps_each_network_is_read_rather_than_built(self):
        networks = {network.index: network for network in parse_ssids(WLAN_LIST)}
        self.assertEqual(
            networks[2].domain,
            "InternetGatewayDevice.LANDevice.1.WLANConfiguration.2",
        )

    def test_a_page_that_is_not_the_filter_page_gives_nothing_rather_than_raising(self):
        state = parse_block_state("<html>a login screen</html>")
        self.assertEqual(state.entries, ())
        self.assertEqual(state.token, "")


class BlockingTests(unittest.TestCase):
    def test_a_device_is_blocked_on_every_network_the_router_has(self):
        # A rule on the 2.4 GHz network alone leaves the phone free to join the
        # 5 GHz one, which would read as a block that does not work. The Guest
        # network is switched off and is written to anyway: switching it back
        # on must not be a way past the block.
        router = FakeRouter()
        BlockList(router).block(PHONE, "Adam's phone")
        self.assertEqual(
            sorted(ssid for _domain, ssid, _mac in router.rules),
            ["SSID-1", "SSID-2", "SSID-5", "SSID-6"],
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

    def test_the_token_is_the_last_thing_in_every_write(self):
        # The rule that a working block was broken by: the same add is accepted
        # with the token at the end and refused with anything after it. The
        # wireless form has an SSID and the wired one does not, so the field
        # that used to come last was not the same on both.
        router = BothRoadsRouter()
        BlockList(router).block(PHONE, "Adam's phone")
        BlockList(router).unblock(PHONE)
        self.assertEqual(router.refused_field_order, 0)
        self.assertTrue(router.writes)

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


class BothRoadsRouter(FakeRouter):
    """A router that keeps both lists, as this firmware really does.

    The wired page is the wireless one written twice by the same people: same
    token, same three forms, different names for the two settings, no SSID, and
    - the trap - the fields of a row in a different order.
    """

    def __init__(self, on: bool = False, blacklist: bool = True):
        super().__init__(on=on, blacklist=blacklist)
        self.wired_on = on
        self.wired_rules: list[tuple[str, str]] = []  # domain, mac
        self.wired_id = 1
        # A wired list that will not take the change: full, or expired, or one
        # of the several ways the second half of a block fails after the first
        # half has already gone through.
        self.refuse_wired = False
        # A wireless list that fills up partway through the networks.
        self.refuse_wifi_after = None

    def wired_page(self) -> str:
        rows = ",".join(
            # (domain, address, name): the address second, where the wireless
            # page puts an SSID.
            f"new stMacFilter(\"{domain}\",\"{mac}\",\"a name\")"
            for domain, mac in self.wired_rules
        )
        return (
            "<html><script>"
            # The same two settings as the wireless page, under the names this
            # page happens to use for them. Read by one spelling only, this
            # switch was never found, the list read as off, and every rule on
            # it - real rules, refusing real devices - read as no rule at all.
            f"var MacFilterEnable = '{1 if self.wired_on else 0}';"
            f"var MacFilterMode = '{0 if self.blacklist else 1}';"
            f"var MacFilter = new Array({rows});"
            "</script>"
            f'<input type="hidden" name="onttoken" id="hwonttoken" value="{self.token}">'
            "</html>"
        )

    def read(self, path):
        if path == WIRED_PAGE:
            return 200, self.wired_page()
        return super().read(path)

    def post(self, path, body, referer=""):
        import urllib.parse as parsing

        if path in {WIRED_ADD, WIRED_DELETE, WIRED_SWITCH}:
            fields = dict(parsing.parse_qsl(body.decode(), keep_blank_values=True))
            self.writes.append((path, fields))
            if self.refuse_wired and path == WIRED_ADD:
                return 403, "that list is full"
            if fields.get("x.X_HW_Token") != self.token:
                self.refused_stale_token += 1
                return 403, "stale token"
            self.token = f"token-{len(self.writes)}"
            if path == WIRED_ADD:
                domain = f"InternetGatewayDevice.X_HW_Security.MacFilter.{self.wired_id}"
                self.wired_id += 1
                self.wired_rules.append((domain, fields["x.SourceMACAddress"]))
            elif path == WIRED_DELETE:
                targets = {key for key in fields if key.startswith("InternetGatewayDevice")}
                self.wired_rules = [r for r in self.wired_rules if r[0] not in targets]
            else:
                self.wired_on = fields["x.MacFilterRight"] == "1"
            return 200, "OK"
        return super().post(path, body, referer)


class WiredTests(unittest.TestCase):
    """Blocking by cable as well as by radio."""

    def test_a_row_is_read_though_its_fields_are_in_another_order(self):
        # The wired page writes (domain, address, name). Counting along the row
        # would take "a name" for the address and find nothing at all.
        router = BothRoadsRouter()
        router.wired_rules = [("InternetGatewayDevice.X_HW_Security.MacFilter.1", "A2:27:EC:61:6A:A6")]
        state = parse_block_state(router.wired_page())
        self.assertEqual([entry.mac for entry in state.entries], [PHONE])

    def test_blocking_writes_to_both_lists(self):
        # A phone that is blocked on the Wi-Fi and then plugged in is not
        # blocked at all.
        router = BothRoadsRouter()
        BlockList(router).block(PHONE, "Adam's phone")
        self.assertEqual(len(router.rules), 4)  # one per SSID the router has
        self.assertEqual(len(router.wired_rules), 1)  # a cable is not chosen

    def test_the_wired_form_is_given_the_names_it_uses(self):
        router = BothRoadsRouter()
        BlockList(router).block(PHONE, "Adam's phone")
        _path, fields = next(
            (path, fields) for path, fields in router.writes if path == WIRED_ADD
        )
        self.assertEqual(fields["x.DeviceAlias"], "Adam's phone")
        self.assertNotIn("x.SSIDName", fields)

    def test_both_switches_are_turned_on(self):
        router = BothRoadsRouter(on=False)
        BlockList(router).block(PHONE)
        self.assertTrue(router.on)
        self.assertTrue(router.wired_on)

    def test_unblocking_clears_both(self):
        router = BothRoadsRouter()
        blocking = BlockList(router)
        blocking.block(PHONE)
        blocking.unblock(PHONE)
        self.assertEqual(router.rules, [])
        self.assertEqual(router.wired_rules, [])

    def test_who_is_blocked_counts_either_list(self):
        router = BothRoadsRouter()
        BlockList(router).block(PHONE)
        self.assertEqual(BlockList(router).blocked_macs(), (PHONE,))

    def test_a_device_refused_by_one_list_reads_as_blocked(self):
        # Refused anywhere is blocked. This used to want both lists, on the
        # reasoning that the missing half is the road the device is still
        # using - but the half-blocked device that turns up in practice is the
        # phone whose Wi-Fi rules were written and whose wired rules were not,
        # and calling that one "online" left it refused with nothing in Mind
        # offering to let it back on.
        router = BothRoadsRouter()
        router.wired_on = True
        router.wired_rules = [
            ("InternetGatewayDevice.X_HW_Security.MacFilter.1", "A2:27:EC:61:6A:A6")
        ]
        self.assertEqual(BlockList(router).blocked_macs(), (PHONE,))

    def test_a_half_block_can_be_let_back_on(self):
        # The point of seeing it: the way out. Rules on one list only are
        # cleared by the same unblock as any other.
        router = BothRoadsRouter()
        router.rules = [
            ("InternetGatewayDevice.X_HW_Security.WLANMacFilter.1", "SSID-1", "A2:27:EC:61:6A:A6")
        ]
        router.on = True
        blocking = BlockList(router)
        self.assertEqual(blocking.blocked_macs(), (PHONE,))
        blocking.unblock(PHONE)
        self.assertEqual(router.rules, [])
        self.assertEqual(BlockList(router).blocked_macs(), ())

    def test_the_wired_settings_are_read_though_that_page_names_them_its_own_way(self):
        # The two pages keep the same two settings under different names. Read
        # by one spelling, the wired switch was never found and its rules read
        # as inert - devices the router really was refusing, invisible.
        router = BothRoadsRouter()
        router.wired_on = True
        state = parse_block_state(router.wired_page())
        self.assertTrue(state.on)
        self.assertTrue(state.switch_read)
        self.assertTrue(state.blacklist)

    def test_the_clearer_of_two_names_is_the_one_read(self):
        # A page that declares both is read by the one that says what it means,
        # whichever it happened to write first.
        page = (
            "<html><script>"
            "var WlanMacFilterRight = '0';"
            "var enableFilter = '1';"
            'var MacFilter = new Array(new stMacFilter("D.1","SSID-1","n","A2:27:EC:61:6A:A6"));'
            "</script>"
            '<input type="hidden" name="onttoken" value="t">'
            "</html>"
        )
        self.assertTrue(parse_block_state(page).on)

    def test_a_switch_that_cannot_be_found_is_not_read_as_off(self):
        # A page Mind cannot read the switch on still holds the rules. Taking
        # them for nothing is what hides a real block; a rule that shows up and
        # can be removed is the smaller mistake.
        page = (
            "<html><script>"
            "var Whatever = '0';"
            'var MacFilter = new Array(new stMacFilter("D.1","A2:27:EC:61:6A:A6","a name"));'
            "</script>"
            '<input type="hidden" name="onttoken" value="t">'
            "</html>"
        )
        state = parse_block_state(page)
        self.assertFalse(state.switch_read)
        self.assertTrue(state.blocks(PHONE))

    def test_a_block_that_fails_on_the_second_list_leaves_the_first_alone(self):
        # Otherwise the phone is off the Wi-Fi under a message saying the block
        # failed: refused, and its owner told there is nothing to undo.
        router = BothRoadsRouter()
        router.refuse_wired = True
        with self.assertRaises(RouterError) as caught:
            BlockList(router).block(PHONE, "Adam's phone")
        self.assertIn("Nothing was changed", str(caught.exception))
        self.assertEqual(router.rules, [])
        self.assertEqual(router.wired_rules, [])
        self.assertFalse(router.on)
        self.assertEqual(BlockList(router).blocked_macs(), ())

    def test_a_list_that_fails_halfway_through_itself_is_taken_back_too(self):
        # Four networks, and the third refused. The two rules already written
        # are a phone refused on half the Wi-Fi, which is a phone that cannot
        # get on the Wi-Fi - under a message saying the block did not work.
        router = BothRoadsRouter()
        router.refuse_wifi_after = 2
        with self.assertRaises(RouterError) as caught:
            BlockList(router).block(PHONE)
        self.assertIn("Nothing was changed", str(caught.exception))
        self.assertEqual(router.rules, [])
        self.assertFalse(router.on)

    def test_a_failed_block_leaves_an_earlier_block_standing(self):
        # Only what this attempt added is taken back. Someone else's rules, and
        # the switch that is holding them up, are not this change's to undo.
        other = "b8-27-eb-11-22-33"
        router = BothRoadsRouter()
        BlockList(router).block(other)
        router.refuse_wired = True
        with self.assertRaises(RouterError):
            BlockList(router).block(PHONE)
        self.assertTrue(router.on)
        self.assertEqual(BlockList(router).blocked_macs(), (other,))

    def test_a_network_written_back_under_another_spelling_is_not_written_twice(self):
        # The form is given SSID-2 and the row can come back as SSID2. Taken
        # for different networks, every block adds four more rules until the
        # list is full and nothing can be blocked at all.
        router = BothRoadsRouter()
        router.echo_ssid_without_the_hyphen = True
        blocking = BlockList(router)
        blocking.block(PHONE)
        before = len(router.rules)
        blocking.block(PHONE)
        self.assertEqual(len(router.rules), before)

    def test_a_device_blocked_on_both_lists_reads_as_blocked(self):
        router = BothRoadsRouter()
        BlockList(router).block(PHONE)
        self.assertEqual(BlockList(router).blocked_macs(), (PHONE,))

    def test_one_list_is_enough_on_a_router_that_only_has_one(self):
        # Nothing is half done where there is no other half.
        router = FakeRouter()
        BlockList(router).block(PHONE)
        self.assertEqual(BlockList(router).blocked_macs(), (PHONE,))

    def test_a_router_with_only_one_list_is_not_an_error(self):
        # FakeRouter answers 404 for the wired page, which is what a firmware
        # without one does.
        router = FakeRouter()
        BlockList(router).block(PHONE)
        self.assertEqual(BlockList(router).kinds()[0].key, "wifi")
        self.assertEqual(len(BlockList(router).kinds()), 1)

    def test_a_whitelist_on_either_list_is_refused(self):
        router = BothRoadsRouter(blacklist=False)
        with self.assertRaises(RouterError):
            BlockList(router).block(PHONE)


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
        status = self.page.COLUMNS.index("Status")
        self.assertEqual(self.page.table.item(0, status).text(), "Blocked")

    def test_the_button_offers_the_way_the_click_would_go(self):
        # And says which device it means: "Block" and "Block Adams phone" are
        # different questions, and only one can be answered without looking
        # away from the button to check what is selected.
        self.show([self.phone])
        self.assertEqual(self.page.block_button.text(), "Block Adams phone")
        self.show([self.phone], blocked=[PHONE])
        self.assertEqual(self.page.block_button.text(), "Let Adams phone back on")

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
