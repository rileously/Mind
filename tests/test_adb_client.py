"""Talking to a phone over ADB.

No phone is attached during a test run, and none is needed: what is tested is
the reading and the refusing. adb is replaced by a function that records what it
was asked and answers with what a real one says - including the three failures
that are almost every failure in practice, each of which needs the user to do
something different.

The one rule with teeth: nothing here builds a shell string. A phone number or a
device name with a space in it must arrive as an argument, or it stops being a
phone number and becomes a command.
"""

import unittest
import unittest.mock

from mind.adb_client import (
    AdbError,
    AndroidDevice,
    CallState,
    Phone,
    parse_call_state,
    parse_devices,
)


DEVICES_OUTPUT = """List of devices attached
39121FDJH0093C         device product:husky model:Pixel_8_Pro device:husky transport_id:3
192.168.18.11:37419    device product:comet model:Pixel_10 device:comet transport_id:5
4A2B1C0D               unauthorized usb:1-4
"""

RINGING = """
  Phone Id=0 mCallState=1 mCallIncomingNumber=+9607771234 mServiceState=0
"""
IN_A_CALL = "mCallState=2 mCallIncomingNumber=null"
IDLE = "mCallState=0"


class Recorder:
    """Stands in for adb, and remembers what it was told to do."""

    def __init__(self, answers=None, code: int = 0, error: str = ""):
        self.answers = dict(answers or {})
        self.code = code
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(self, arguments, timeout):
        self.calls.append(list(arguments))
        for needle, answer in self.answers.items():
            if needle in " ".join(arguments):
                return 0, answer, ""
        return self.code, "", self.error

    @property
    def last(self) -> list[str]:
        return self.calls[-1]


def phone(**kwargs) -> tuple[Phone, Recorder]:
    recorder = Recorder(**kwargs)
    return Phone(adb="adb.exe", run=recorder), recorder


class ListingTests(unittest.TestCase):
    def test_every_phone_is_read_with_the_name_it_goes_by(self):
        found = parse_devices(DEVICES_OUTPUT)
        self.assertEqual(len(found), 3)
        self.assertEqual(found[1].display_name, "Pixel 10")

    def test_a_phone_that_has_not_trusted_this_pc_is_kept_in_the_list(self):
        # It is the most common thing to go wrong, and hiding it would leave
        # the user with "no phone" when the phone is right there.
        unauthorised = [device for device in parse_devices(DEVICES_OUTPUT) if not device.ready]
        self.assertEqual(len(unauthorised), 1)
        self.assertEqual(unauthorised[0].state, "unauthorized")

    def test_a_wireless_phone_is_known_from_a_plugged_in_one(self):
        found = {device.serial: device for device in parse_devices(DEVICES_OUTPUT)}
        self.assertTrue(found["192.168.18.11:37419"].over_wifi)
        self.assertFalse(found["39121FDJH0093C"].over_wifi)

    def test_nothing_attached_reads_as_nothing_rather_than_raising(self):
        self.assertEqual(parse_devices("List of devices attached\n\n"), [])
        self.assertEqual(parse_devices(""), [])


class CallStateTests(unittest.TestCase):
    def test_a_ringing_phone_is_recognised(self):
        state = parse_call_state(RINGING)
        self.assertTrue(state.ringing)
        self.assertEqual(state.number, "+9607771234")

    def test_a_call_in_progress_is_busy_but_not_ringing(self):
        state = parse_call_state(IN_A_CALL)
        self.assertTrue(state.busy)
        self.assertFalse(state.ringing)

    def test_an_idle_phone_is_neither(self):
        self.assertFalse(parse_call_state(IDLE).busy)

    def test_a_redacted_number_still_leaves_a_usable_state(self):
        # Newer Android hides the number from the shell. Knowing that the phone
        # is ringing is most of the value and must not depend on knowing who.
        state = parse_call_state("mCallState=1 mCallIncomingNumber=")
        self.assertTrue(state.ringing)
        self.assertEqual(state.number, "")

    def test_output_from_something_else_entirely_reads_as_idle(self):
        self.assertEqual(parse_call_state("<html>not a phone</html>"), CallState())


class CommandTests(unittest.TestCase):
    def test_answering_sends_the_headset_hook_and_never_the_call_key(self):
        # Pressed against a ringing Pixel on Android 17, the call key ended the
        # call instead of connecting it. It must not be part of answering.
        device, recorder = phone(answers={"telephony.registry": IDLE})
        self.assertTrue(device.answer())
        self.assertIn(["adb.exe", "shell", "input", "keyevent", "79"], recorder.calls)
        self.assertNotIn(["adb.exe", "shell", "input", "keyevent", "5"], recorder.calls)

    def test_a_phone_still_ringing_gets_its_answer_button_tapped(self):
        # Where the button is differs by dialer and differs again on the lock
        # screen, so it is found by what it says.
        screen = (
            '<node text="Answer" clickable="true" bounds="[100,900][300,1000]" />'
            '<node text="Decline" clickable="true" bounds="[600,900][800,1000]" />'
        )
        device, recorder = phone(answers={"telephony.registry": RINGING, "cat": screen})
        device.answer()
        self.assertIn(["adb.exe", "shell", "input", "tap", "200", "950"], recorder.calls)

    def test_the_decline_button_is_never_the_one_tapped(self):
        screen = (
            '<node text="Decline" clickable="true" bounds="[600,900][800,1000]" />'
            '<node text="Answer" clickable="true" bounds="[100,900][300,1000]" />'
        )
        device, recorder = phone(answers={"telephony.registry": RINGING, "cat": screen})
        device.answer()
        taps = [call for call in recorder.calls if "tap" in call]
        self.assertEqual(taps, [["adb.exe", "shell", "input", "tap", "200", "950"]])

    def test_a_screen_with_nothing_to_answer_gives_up_rather_than_tapping_blindly(self):
        device, recorder = phone(answers={"telephony.registry": RINGING, "cat": "<node text='Home' />"})
        self.assertFalse(device.answer())
        self.assertFalse([call for call in recorder.calls if "tap" in call])

    def test_hanging_up_presses_the_end_key(self):
        device, recorder = phone()
        device.hang_up()
        self.assertEqual(recorder.last, ["adb.exe", "shell", "input", "keyevent", "6"])

    def test_the_chosen_phone_is_named_on_every_command(self):
        recorder = Recorder()
        Phone(serial="192.168.18.11:37419", adb="adb.exe", run=recorder).hang_up()
        self.assertEqual(recorder.last[:3], ["adb.exe", "-s", "192.168.18.11:37419"])

    def test_dialling_sends_the_number_as_one_argument(self):
        # The rule with teeth. A number is data; it must never be able to end
        # the command and begin another.
        device, recorder = phone()
        device.dial("+960 777-1234")
        self.assertIn("tel:+9607771234", recorder.last)
        self.assertEqual(len([arg for arg in recorder.last if arg.startswith("tel:")]), 1)

    def test_something_that_is_not_a_number_is_refused_before_anything_is_sent(self):
        device, recorder = phone()
        for written in ("", "rm -rf /", "; reboot", "hello"):
            with self.assertRaises(AdbError):
                device.dial(written)
        self.assertEqual(recorder.calls, [])

    def test_a_media_key_it_does_not_know_is_refused(self):
        device, recorder = phone()
        with self.assertRaises(AdbError):
            device.press_media("explode")
        self.assertEqual(recorder.calls, [])

    def test_the_battery_is_read_out_of_what_dumpsys_prints(self):
        device, _ = phone(answers={"battery": "  level: 74\n  scale: 100"})
        self.assertEqual(device.battery(), 74)

    def test_a_phone_that_will_not_say_gives_a_number_rather_than_an_error(self):
        device, _ = phone(answers={"battery": "nothing useful here"})
        self.assertEqual(device.battery(), -1)


class FailureTests(unittest.TestCase):
    """adb's own words, turned into something worth reading."""

    def test_an_untrusted_phone_says_to_look_at_the_phone(self):
        device, _ = phone(code=1, error="adb: device unauthorized.")
        with self.assertRaises(AdbError) as caught:
            device.hang_up()
        self.assertIn("accept the debugging prompt", str(caught.exception))

    def test_nothing_attached_says_so_plainly(self):
        device, _ = phone(code=1, error="adb: no devices/emulators found")
        with self.assertRaises(AdbError) as caught:
            device.hang_up()
        self.assertIn("No phone is connected", str(caught.exception))

    def test_two_phones_ask_which_one(self):
        device, _ = phone(code=1, error="adb: more than one device/emulator")
        with self.assertRaises(AdbError) as caught:
            device.hang_up()
        self.assertIn("Choose which one", str(caught.exception))

    def test_adb_that_is_not_installed_says_that_rather_than_failing_oddly(self):
        # An empty path is filled in by looking for adb, so the machine running
        # the tests must be made to look like one without it.
        with unittest.mock.patch("mind.adb_client.find_adb", return_value=""):
            device = Phone(run=Recorder())
        with self.assertRaises(AdbError) as caught:
            device.hang_up()
        self.assertIn("platform tools", str(caught.exception))


class PairingTests(unittest.TestCase):
    def test_an_address_without_a_port_is_refused(self):
        device, recorder = phone()
        with self.assertRaises(AdbError):
            device.pair("192.168.18.5", "123456")
        self.assertEqual(recorder.calls, [])

    def test_a_code_that_is_not_six_digits_is_refused(self):
        device, recorder = phone()
        with self.assertRaises(AdbError):
            device.pair("192.168.18.5:37000", "12")
        self.assertEqual(recorder.calls, [])

    def test_a_good_pairing_is_passed_through_as_typed(self):
        device, recorder = phone(answers={"pair": "Successfully paired"})
        device.pair("192.168.18.5:37000", "123456")
        self.assertEqual(recorder.last, ["adb.exe", "pair", "192.168.18.5:37000", "123456"])

    def test_a_connection_that_failed_is_not_reported_as_success(self):
        # adb exits 0 while printing "unable to connect", which would otherwise
        # read as a phone that is ready.
        device, _ = phone(answers={"connect": "unable to connect to 192.168.18.5:5555"})
        with self.assertRaises(AdbError):
            device.connect("192.168.18.5:5555")


if __name__ == "__main__":
    unittest.main()
