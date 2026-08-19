"""Sharing this PC's Wi-Fi, without a radio to share.

No hotspot comes up during a test run and none is needed: what is tested is the
reading of what Windows said and the refusing of what it will not take. The
script is replaced by a function that answers the way the real one does,
including the failures - an adapter that cannot host, a password Windows will
not accept, and the "InTransition" that means the radio is still on its way up.

The rule with teeth is the last test: a password read from netsh is passed as an
argument and never built into a command line, because a Wi-Fi password with a
space or a quote in it would otherwise stop being a password.
"""

import unittest

from mind.hotspot import (
    Hotspot,
    HotspotError,
    HotspotState,
    current_wifi,
    dhcp_fault,
    parse_dhcp_addresses,
    parse_current_ssid,
    parse_profile_key,
    parse_report,
)


ON = "ok=1\nstate=On\nclients=2\nssid=Dhipoz\n"
OFF = "ok=1\nstate=Off\nclients=0\nssid=DESKTOP 1234\n"
COMING_UP = "ok=1\nstate=InTransition\nclients=0\nssid=Dhipoz\n"
REFUSED = "ok=0\ndetail=This adapter cannot host a hotspot. Its driver may not support one.\n"

INTERFACES = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201
    State                  : connected
    SSID                   : Dhipoz
    BSSID                  : a4:2b:8c:11:22:33
    Radio type             : 802.11ac
    Signal                 : 71%
"""
PROFILE = """
Profile Dhipoz on interface Wi-Fi:

    SSID name              : "Dhipoz"
    Security settings
        Authentication     : WPA2-Personal
        Key Content        : correct horse battery
"""


class Recorder:
    """Stands in for the script, remembering what it was asked."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, arguments, timeout):
        self.calls.append(list(arguments))
        answer = self.answers.pop(0) if self.answers else ""
        return 0, answer, ""


class ReadingTheReport(unittest.TestCase):
    def test_an_access_point_that_is_up(self):
        state = parse_report(ON)
        self.assertEqual(state, HotspotState(state="on", clients=2, ssid="Dhipoz"))
        self.assertTrue(state.is_on)

    def test_one_that_is_down(self):
        state = parse_report(OFF)
        self.assertFalse(state.is_on)
        self.assertEqual(state.clients, 0)

    def test_one_still_coming_up_is_neither(self):
        state = parse_report(COMING_UP)
        self.assertFalse(state.is_on)
        self.assertTrue(state.is_changing)

    def test_a_refusal_arrives_as_its_sentence(self):
        with self.assertRaises(HotspotError) as caught:
            parse_report(REFUSED)
        self.assertIn("cannot host", str(caught.exception))

    def test_silence_is_a_failure_rather_than_an_off_hotspot(self):
        # An empty answer means the script did not run. Reporting that as "off"
        # would put a Turn on button in the chat that could never work.
        with self.assertRaises(HotspotError):
            parse_report("")


class ReadingNetsh(unittest.TestCase):
    def test_the_network_this_pc_is_on(self):
        self.assertEqual(parse_current_ssid(INTERFACES), "Dhipoz")

    def test_bssid_is_not_mistaken_for_ssid(self):
        # It sits directly under SSID and would match a looser pattern.
        self.assertNotIn(":", parse_current_ssid(INTERFACES))

    def test_the_saved_password(self):
        self.assertEqual(parse_profile_key(PROFILE), "correct horse battery")

    def test_a_profile_without_one(self):
        self.assertEqual(parse_profile_key("    Security settings\n"), "")


class DrivingTheRadio(unittest.TestCase):
    def test_turning_it_on(self):
        run = Recorder(ON)
        state = Hotspot(run=run).start()
        self.assertTrue(state.is_on)
        self.assertIn("-Action", run.calls[0])
        self.assertIn("start", run.calls[0])

    def test_turning_it_off(self):
        run = Recorder(OFF)
        self.assertFalse(Hotspot(run=run).stop().is_on)

    def test_a_password_windows_would_not_take_is_refused_here_first(self):
        run = Recorder(ON)
        with self.assertRaises(HotspotError):
            Hotspot(run=run).configure("Dhipoz", "short")
        self.assertEqual(run.calls, [])

    def test_a_hotspot_needs_a_name(self):
        run = Recorder(ON)
        with self.assertRaises(HotspotError):
            Hotspot(run=run).configure("   ", "long enough to pass")
        self.assertEqual(run.calls, [])


class MatchingTheHomeNetwork(unittest.TestCase):
    def test_the_name_and_password_are_read_together(self):
        run = Recorder(INTERFACES, PROFILE)
        self.assertEqual(current_wifi(run=run), ("Dhipoz", "correct horse battery"))

    def test_a_pc_on_a_cable_reports_nothing_rather_than_failing(self):
        run = Recorder("There is 0 interfaces on the system:\n")
        self.assertEqual(current_wifi(run=run), ("", ""))

    def test_a_password_with_spaces_stays_one_argument(self):
        run = Recorder(ON)
        Hotspot(run=run).configure("Dhipoz", "correct horse battery")
        arguments = run.calls[0]
        self.assertIn("correct horse battery", arguments)
        # Never assembled into a string that a shell would then split.
        self.assertTrue(all(" " not in part for part in arguments if part.endswith(".ps1")))


class AnOpenHotspotIsNotOffered(unittest.TestCase):
    """Windows has no open hotspot, so neither does Mind.

    TetheringWiFiAuthenticationKind carries Wpa2, Wpa3TransitionMode and Wpa3
    and nothing else: there is no value meaning "no password". The refusal
    therefore belongs here, where it can say so, rather than at WinRT, where it
    arrives as a failed operation with no explanation.
    """

    def test_no_password_is_refused_before_windows_sees_it(self):
        run = Recorder(ON)
        with self.assertRaises(HotspotError):
            Hotspot(run=run).configure("Toilet", "")
        self.assertEqual(run.calls, [])

    def test_the_refusal_says_what_would_be_acceptable(self):
        with self.assertRaises(HotspotError) as caught:
            Hotspot(run=Recorder(ON)).configure("Toilet", "1234567")
        self.assertIn("8", str(caught.exception))


class ANameOfItsOwn(unittest.TestCase):
    """A hotspot that is not pretending to be the home network."""

    def test_a_name_and_key_are_sent_as_given(self):
        run = Recorder(ON)
        Hotspot(run=run).configure("Toilet Wi-Fi", "openthedoor")
        self.assertIn("Toilet Wi-Fi", run.calls[0])
        self.assertIn("openthedoor", run.calls[0])

    def test_surrounding_space_in_a_name_is_not_kept(self):
        run = Recorder(ON)
        Hotspot(run=run).configure("  Toilet  ", "openthedoor")
        self.assertIn("Toilet", run.calls[0])

    def test_but_a_password_is_left_exactly_as_typed(self):
        # A Wi-Fi key may legitimately end in a space. Trimming it would leave a
        # hotspot that refuses the password its owner believes they set.
        run = Recorder(ON)
        Hotspot(run=run).configure("Toilet", "open door ")
        self.assertIn("open door ", run.calls[0])


NETSTAT_SERVED = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  UDP    0.0.0.0:53             *:*                                    3872
  UDP    172.24.240.1:67        *:*                                    3872
  UDP    192.168.137.1:67       *:*                                    3872
"""
NETSTAT_ELSEWHERE = """
  Proto  Local Address          Foreign Address        State           PID
  UDP    0.0.0.0:53             *:*                                    3872
  UDP    172.24.240.1:67        *:*                                    3872
"""
NETSTAT_NOBODY = """
  Proto  Local Address          Foreign Address        State           PID
  UDP    0.0.0.0:53             *:*                                    3872
"""


class HandingOutAddresses(unittest.TestCase):
    """Whether a device that joins can expect an address.

    Everything else can look right while this is wrong: the radio up, the name
    broadcast, the password accepted, and the phone still saying its IP
    configuration failed - a sentence with nothing in it about which end is at
    fault. Internet Connection Sharing serves one allocator, and another
    virtual switch can be holding it.
    """

    def test_the_port_is_read_off_every_listening_address(self):
        self.assertEqual(
            parse_dhcp_addresses(NETSTAT_SERVED), ("172.24.240.1", "192.168.137.1")
        )

    def test_ports_that_merely_end_in_67_are_not_counted(self):
        payload = "  UDP    192.168.137.1:1067     *:*                     3872"
        self.assertEqual(parse_dhcp_addresses(payload), ())

    def test_tcp_is_not_dhcp(self):
        payload = "  TCP    192.168.137.1:67       0.0.0.0:0     LISTENING   3872"
        self.assertEqual(parse_dhcp_addresses(payload), ())

    def test_nothing_is_wrong_when_the_hotspot_is_served(self):
        self.assertEqual(dhcp_fault(run=Recorder(NETSTAT_SERVED), wait=0), "")

    def test_another_network_holding_it_is_named(self):
        fault = dhcp_fault(run=Recorder(NETSTAT_ELSEWHERE), wait=0)
        self.assertIn("172.24.240.1", fault)
        self.assertIn("IP configuration failed", fault)

    def test_nobody_serving_it_reads_differently(self):
        fault = dhcp_fault(run=Recorder(NETSTAT_NOBODY), wait=0)
        self.assertIn("not handing out addresses", fault)

    def test_not_being_able_to_look_is_not_a_fault(self):
        # A warning under a working hotspot is worse than no warning at all.
        def refuse(arguments, timeout):
            raise HotspotError("netstat is not on this machine")

        self.assertEqual(dhcp_fault(run=refuse, wait=0), "")


if __name__ == "__main__":
    unittest.main()
