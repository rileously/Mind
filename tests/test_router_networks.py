"""Putting a whole Wi-Fi network on or off the air.

Blocking names one device. This is the other end of the same wish: a guest
network or a children's network that should simply not be there for a while,
whoever owns the phone trying to join it.

The router is never actually spoken to here. A fake one answers the way the
firmware does - including the two ways it refuses a write, which are the reason
blocking took three attempts to get right - so that the rules those refusals
imply are held to rather than rediscovered.
"""

from __future__ import annotations

import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mind.router_client import (  # noqa: E402
    RouterError,
    RouterSession,
    Ssid,
    WLAN_LIST_PAGE,
    WlanNetworks,
    parse_devices,
    parse_ssids,
)


WLAN_PAGE = "/html/bbsp/wlanbasic/wlanbasic.asp"


def wlan_list(enabled: dict[int, bool]) -> str:
    """The router's WLAN list with these networks in these states."""
    names = {1: "Home 2.4G", 2: "Home kids", 5: "Home 5G", 6: "Guest"}
    bands = {1: "2.4GHz", 2: "2.4GHz", 5: "5GHz", 6: "5GHz"}
    rows = ",".join(
        'new stWlanInfo("InternetGatewayDevice.LANDevice.1.WLANConfiguration.'
        f'{index}","ath{index}","{names[index]}","{1 if on else 0}","1","{bands[index]}")'
        for index, on in sorted(enabled.items())
    )
    return f"var WlanInfo = new Array({rows});"


class FakeRouter(RouterSession):
    """Answers like the firmware, and keeps what it is told."""

    def __init__(self, enabled: dict[int, bool] | None = None, form: str = WLAN_PAGE):
        super().__init__("192.168.18.1")
        self.enabled = dict(enabled or {1: True, 2: True, 5: True, 6: False})
        self.form_path = form
        self.token = "token-0"
        self.writes: list[tuple[str, dict]] = []
        self.refused_stale_token = 0
        self.refused_field_order = 0
        self.deaf = False  # takes the write, changes nothing

    def read(self, path):
        if path == WLAN_LIST_PAGE:
            return 200, wlan_list(self.enabled)
        if self.form_path and path == self.form_path:
            return (
                200,
                "<html><body>the Wi-Fi settings"
                f'<input type="hidden" name="onttoken" value="{self.token}">'
                "</body></html>",
            )
        return 404, "not found"

    def post(self, path, body, referer=""):
        pairs = urllib.parse.parse_qsl(body.decode(), keep_blank_values=True)
        fields = dict(pairs)
        self.writes.append((path, fields))
        if pairs and pairs[-1][0] != "x.X_HW_Token":
            self.refused_field_order += 1
            return 403, "the token must come last"
        if fields.get("x.X_HW_Token") != self.token:
            self.refused_stale_token += 1
            return 403, "stale token"
        self.token = f"token-{len(self.writes)}"
        target = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query).get("x", [""])[0]
        index = target.rsplit(".", 1)[-1]
        if not index.isdigit():
            return 404, "no such object"
        if not self.deaf:
            self.enabled[int(index)] = fields.get("x.Enable") == "1"
        return 200, "OK"


class ReadingTests(unittest.TestCase):
    """What is on the air, and what merely exists."""

    def test_every_network_is_listed_whether_it_is_on_or_not(self):
        found = WlanNetworks(FakeRouter()).all()
        self.assertEqual([network.index for network in found], [1, 2, 5, 6])

    def test_whether_each_is_on_the_air_comes_back_with_it(self):
        found = {network.index: network.enabled for network in WlanNetworks(FakeRouter()).all()}
        self.assertEqual(found, {1: True, 2: True, 5: True, 6: False})

    def test_a_network_says_what_a_person_would_call_it(self):
        found = {network.index: network for network in WlanNetworks(FakeRouter()).all()}
        self.assertEqual(found[2].label, "Home kids (2.4GHz)")

    def test_a_network_with_no_name_falls_back_to_its_number(self):
        self.assertEqual(Ssid(3).label, "SSID-3")

    def test_the_three_spellings_of_one_network_agree(self):
        # The filter form hyphenates, the device rows do not, and neither is
        # the object path. All three name network two.
        network = Ssid(2, "Home kids")
        self.assertEqual(network.field, "SSID-2")
        self.assertEqual(network.port, "SSID2")

    def test_listing_works_on_a_router_whose_settings_page_is_missing(self):
        # Reading needs no form. A firmware that keeps its Wi-Fi settings
        # somewhere Mind cannot find must still show what is on the air.
        found = WlanNetworks(FakeRouter(form="")).all()
        self.assertEqual(len(found), 4)


class SwitchingTests(unittest.TestCase):
    def test_a_network_is_taken_off_the_air(self):
        router = FakeRouter()
        after = WlanNetworks(router).set_enabled(2, False)
        self.assertFalse(after.enabled)
        self.assertFalse(router.enabled[2])

    def test_a_network_that_was_off_is_put_back_on(self):
        router = FakeRouter()
        after = WlanNetworks(router).set_enabled(6, True)
        self.assertTrue(after.enabled)
        self.assertTrue(router.enabled[6])

    def test_only_the_network_asked_for_is_touched(self):
        router = FakeRouter()
        WlanNetworks(router).set_enabled(2, False)
        self.assertEqual(router.enabled, {1: True, 2: False, 5: True, 6: False})

    def test_the_write_is_addressed_to_the_object_the_router_named(self):
        router = FakeRouter()
        WlanNetworks(router).set_enabled(2, False)
        path, fields = router.writes[0]
        self.assertIn(
            "x=InternetGatewayDevice.LANDevice.1.WLANConfiguration.2", path
        )
        self.assertEqual(fields["x.Enable"], "0")

    def test_the_write_says_which_page_it_came_from(self):
        # These pages are only ever reached from themselves and the firmware
        # checks it, so a write that does not say so is thrown away silently.
        router = FakeRouter()
        WlanNetworks(router).set_enabled(2, False)
        path, _fields = router.writes[0]
        self.assertIn("RequestFile=html/bbsp/wlanbasic/wlanbasic.asp", path)

    def test_the_token_goes_last_or_the_router_refuses_it(self):
        router = FakeRouter()
        WlanNetworks(router).set_enabled(2, False)
        self.assertEqual(router.refused_field_order, 0)
        _path, fields = router.writes[0]
        self.assertEqual(list(fields)[-1], "x.X_HW_Token")

    def test_the_token_is_the_one_the_page_is_carrying_now(self):
        router = FakeRouter()
        WlanNetworks(router).set_enabled(2, False)
        self.assertEqual(router.refused_stale_token, 0)

    def test_asking_for_what_is_already_true_writes_nothing(self):
        # Network two is already on. Turning it on is not a change, and a form
        # posted for it would spend a token to no purpose.
        router = FakeRouter()
        WlanNetworks(router).set_enabled(2, True)
        self.assertEqual(router.writes, [])


class WhenItGoesWrongTests(unittest.TestCase):
    """A change is believed because the router was asked again, not because it said OK."""

    def test_a_router_that_takes_the_write_and_ignores_it_is_caught(self):
        router = FakeRouter()
        router.deaf = True
        with self.assertRaises(RouterError) as raised:
            WlanNetworks(router).set_enabled(2, False)
        self.assertIn("still on the air", str(raised.exception))

    def test_a_network_the_router_does_not_have_is_refused_before_any_write(self):
        router = FakeRouter()
        with self.assertRaises(RouterError):
            WlanNetworks(router).set_enabled(9, False)
        self.assertEqual(router.writes, [])

    def test_a_firmware_with_no_settings_page_is_told_about_rather_than_guessed_at(self):
        router = FakeRouter(form="")
        with self.assertRaises(RouterError) as raised:
            WlanNetworks(router).set_enabled(2, False)
        self.assertIn("could not find the page", str(raised.exception))
        self.assertEqual(router.writes, [])

    def test_a_page_carrying_no_token_is_not_written_to(self):
        class NoToken(FakeRouter):
            def read(self, path):
                if path == self.form_path:
                    return 200, "<html>the Wi-Fi settings, and no token</html>"
                return super().read(path)

        router = NoToken()
        with self.assertRaises(RouterError):
            WlanNetworks(router).set_enabled(2, False)
        self.assertEqual(router.writes, [])

    def test_a_router_that_lists_no_networks_says_so(self):
        class Empty(FakeRouter):
            def read(self, path):
                if path == WLAN_LIST_PAGE:
                    return 200, "var WlanInfo = new Array();"
                return super().read(path)

        with self.assertRaises(RouterError):
            WlanNetworks(Empty()).all()


class WhichNetworkADeviceIsOnTests(unittest.TestCase):
    """The device rows have always carried this; nothing read it until now.

    Knowing a phone is on the kids' network is what makes switching that
    network off a decision rather than a guess about who it cuts off.
    """

    BODY = (
        "function USERDeviceNew(Domain, IpAddr, MacAddr, Port, IpType, DevType,"
        " DevStatus, PortType, Time, HostName, IPv4Enabled, IPv6Enabled,"
        " DeviceType, UserDevAlias, UserSpecifiedDeviceType, LeaseTimeRemaining,"
        " RealMacAddr) {}"
        "var UserDevinfo = new Array("
        'new USERDeviceNew("InternetGatewayDevice.LANDevice.1.X_HW_UserDev.3",'
        '"192.168.18.12","a2:27:ec:61:6a:a6","SSID2","DHCP","android-dhcp-13",'
        '"Online","WIFI","0:10","Redmi-Note-11","1","1","0","","1","2967",'
        '"a2:27:ec:61:6a:a6"),'
        'new USERDeviceNew("InternetGatewayDevice.LANDevice.1.X_HW_UserDev.4",'
        '"192.168.18.7","90:de:80:77:53:37","LAN1","DHCP","MSFT 5.0",'
        '"Online","LAN","7:45","DESKTOP-2KVG8KH","1","1","0","","0","2676",'
        '"90:de:80:77:53:37"));'
    )

    def setUp(self):
        self.devices = {device.ip: device for device in parse_devices(self.BODY)}

    def test_a_wireless_device_says_which_network_it_joined(self):
        self.assertEqual(self.devices["192.168.18.12"].network, "SSID2")

    def test_a_cable_is_not_a_network_you_can_switch_off(self):
        self.assertEqual(self.devices["192.168.18.7"].network, "")

    def test_it_matches_what_the_network_list_calls_the_same_network(self):
        network = {item.index: item for item in parse_ssids(wlan_list({2: True}))}[2]
        self.assertEqual(network.port, self.devices["192.168.18.12"].network)


if __name__ == "__main__":
    unittest.main()
