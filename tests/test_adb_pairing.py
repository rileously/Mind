"""Pairing a phone by showing it a code.

No phone looks at anything during a test run. What is tested is the code that
is shown, the recognising of the phone that read it, and the refusing - because
the whole exchange rests on a name and a password that have to survive being
put inside a QR payload and come back out meaning the same thing.
"""

import unittest

from mind.adb_pairing import (
    NAME_PREFIX,
    PASSWORD_LENGTH,
    PairingError,
    address_for,
    new_name,
    new_password,
    pair,
    qr_payload,
    wait_for_phone,
)


class TheCodeOnScreen(unittest.TestCase):
    def test_the_payload_is_the_shape_android_reads(self):
        self.assertEqual(
            qr_payload("studio-abcd", "secret123456"),
            "WIFI:T:ADB;S:studio-abcd;P:secret123456;;",
        )

    def test_a_semicolon_would_end_the_field_early(self):
        # It would not fail: the phone would read a different name and wait
        # for a pairing that never comes, which is worse.
        with self.assertRaises(PairingError):
            qr_payload("studio-a;b", "secret123456")
        with self.assertRaises(PairingError):
            qr_payload("studio-ab", "secret;12345")

    def test_nothing_missing(self):
        with self.assertRaises(PairingError):
            qr_payload("", "secret123456")
        with self.assertRaises(PairingError):
            qr_payload("studio-ab", "")

    def test_every_pairing_gets_its_own_password(self):
        self.assertNotEqual(new_password(), new_password())
        self.assertEqual(len(new_password()), PASSWORD_LENGTH)

    def test_every_pairing_gets_its_own_name(self):
        self.assertNotEqual(new_name(), new_name())
        self.assertTrue(new_name().startswith(NAME_PREFIX))

    def test_the_password_is_not_in_the_name(self):
        # They travel together in the code; they must not be the same secret.
        name, password = new_name(), new_password()
        self.assertNotIn(password, name)


class FindingThePhoneThatLooked(unittest.TestCase):
    SERVICES = [
        ("studio-11112222", "192.168.18.5:37000"),
        ("studio-aaaabbbb", "192.168.18.9:41000"),
    ]

    def test_the_phone_that_read_our_code_is_the_one_named(self):
        self.assertEqual(address_for("studio-aaaabbbb", self.SERVICES), "192.168.18.9:41000")

    def test_a_phone_pairing_with_something_else_is_not_ours(self):
        # Two computers can be showing codes at once.
        self.assertEqual(address_for("studio-99998888", self.SERVICES), "")

    def test_nothing_advertising_yet(self):
        self.assertEqual(address_for("studio-11112222", []), "")


class Waiting(unittest.TestCase):
    def test_it_returns_as_soon_as_the_phone_appears(self):
        # Nothing, nothing, then the phone.
        looks = [[], [], [("studio-x", "192.168.18.5:37000")]]
        found = wait_for_phone(
            "studio-x", timeout=99, look=lambda: looks.pop(0), sleep=lambda _s: None,
        )
        self.assertEqual(found, "192.168.18.5:37000")

    def test_nobody_looking_is_not_an_error(self):
        ticks = iter([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
        found = wait_for_phone(
            "studio-x", timeout=3, look=lambda: [], sleep=lambda _s: None,
            now=lambda: next(ticks),
        )
        self.assertEqual(found, "")

    def test_closing_the_window_stops_the_waiting(self):
        calls = []

        def look():
            calls.append(1)
            return []

        found = wait_for_phone(
            "studio-x", timeout=99, look=look, sleep=lambda _s: None,
            keep_going=lambda: False,
        )
        self.assertEqual(found, "")
        self.assertEqual(calls, [])


class Completing(unittest.TestCase):
    def runner(self, code, out):
        def run(arguments, timeout):
            self.arguments = arguments
            return code, out, ""

        return run

    def test_a_successful_pairing_is_reported_in_its_own_words(self):
        spoken = pair(
            "192.168.18.5:37000", "secret123456", adb="adb.exe",
            run=self.runner(0, "Successfully paired to 192.168.18.5:37000 [guid=adb-X]"),
        )
        self.assertIn("Successfully paired", spoken)
        self.assertIn("pair", self.arguments)

    def test_a_refusal_is_raised_rather_than_returned(self):
        with self.assertRaises(PairingError):
            pair("192.168.18.5:37000", "wrong", adb="adb.exe",
                 run=self.runner(1, "failed to authenticate"))

    def test_a_zero_exit_that_did_not_pair_is_still_a_failure(self):
        # adb has said less than it should before now.
        with self.assertRaises(PairingError):
            pair("192.168.18.5:37000", "secret123456", adb="adb.exe",
                 run=self.runner(0, "protocol fault"))

    def test_the_password_is_passed_as_an_argument(self):
        pair("192.168.18.5:37000", "secret 123456", adb="adb.exe",
             run=self.runner(0, "Successfully paired"))
        self.assertIn("secret 123456", self.arguments)


if __name__ == "__main__":
    unittest.main()
