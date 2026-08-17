"""Getting a device's name out of what it says about itself.

The packets are built here rather than waited for, because a phone answers mDNS
when it feels like it and a test cannot depend on the room being awake. What is
checked is the reading: a cast device putting "fn=Living Room TV" in a TXT
record is the best name on the network, and it has to survive name compression,
service suffixes and a malformed reply.
"""

import struct
import unittest

from mind.network_devices import (
    _clean_mdns_name,
    _read_name,
    _txt_value,
    name_from_packet,
)


def encode_name(name: str) -> bytes:
    body = b""
    for label in name.split("."):
        body += bytes([len(label)]) + label.encode()
    return body + b"\x00"


def record(name: str, rtype: int, rdata: bytes) -> bytes:
    return encode_name(name) + struct.pack(">HHIH", rtype, 1, 120, len(rdata)) + rdata


def reply(*records: bytes) -> bytes:
    header = struct.pack(">HHHHHH", 0, 0x8400, 0, len(records), 0, 0)
    return header + b"".join(records)


def txt(*pairs: str) -> bytes:
    body = b""
    for pair in pairs:
        encoded = pair.encode()
        body += bytes([len(encoded)]) + encoded
    return body


class NameFromPacketTests(unittest.TestCase):
    def test_a_friendly_name_is_preferred_over_everything(self):
        packet = reply(
            record("Chromecast-9e5e._googlecast._tcp.local", 16, txt("id=abc", "fn=Living Room TV", "md=Chromecast")),
            record("Chromecast-9e5e.local", 1, bytes([192, 168, 18, 15])),
        )
        self.assertEqual(name_from_packet(packet), "Living Room TV")

    def test_the_instance_name_is_used_when_there_is_no_friendly_one(self):
        packet = reply(
            record("_googlecast._tcp.local", 12, encode_name("Android_0DHJR._googlecast._tcp.local"))
        )
        self.assertEqual(name_from_packet(packet), "Android_0DHJR")

    def test_a_plain_host_name_is_the_last_resort(self):
        packet = reply(record("adams-laptop.local", 1, bytes([192, 168, 18, 4])))
        self.assertEqual(name_from_packet(packet), "adams-laptop")

    def test_the_service_part_is_not_part_of_the_name(self):
        self.assertEqual(_clean_mdns_name("Kitchen Speaker._raop._tcp.local"), "Kitchen Speaker")
        self.assertEqual(_clean_mdns_name("printer._ipp._tcp.local"), "printer")

    def test_a_bare_service_type_is_not_a_device_name(self):
        # "_googlecast._tcp.local" is the question, not an answer about anyone.
        self.assertEqual(_clean_mdns_name("_googlecast._tcp.local"), "")

    def test_a_name_outside_local_is_ignored(self):
        self.assertEqual(_clean_mdns_name("15.18.168.192.in-addr.arpa"), "")

    def test_a_truncated_packet_gives_nothing_rather_than_raising(self):
        # Anything on the network can send anything at all to that port.
        for rubbish in (b"", b"\x00\x01", b"\xff" * 40, reply()[:8]):
            self.assertEqual(name_from_packet(rubbish), "")

    def test_a_compressed_name_is_followed(self):
        # Replies point back at earlier names rather than repeating them, so a
        # parser that cannot follow a pointer reads nonsense.
        first = record("Sonos._raop._tcp.local", 16, txt("fn=Kitchen"))
        pointer = struct.pack(">H", 0xC000 | 12)  # back to the first record's name
        second = pointer + struct.pack(">HHIH", 1, 1, 120, 4) + bytes([10, 0, 0, 5])
        packet = struct.pack(">HHHHHH", 0, 0x8400, 0, 2, 0, 0) + first + second
        self.assertEqual(name_from_packet(packet), "Kitchen")


class TxtTests(unittest.TestCase):
    def test_a_key_is_found_among_the_others(self):
        self.assertEqual(_txt_value(txt("id=abc", "fn=Adam's TV", "ve=05"), "fn"), "Adam's TV")

    def test_a_missing_key_is_empty(self):
        self.assertEqual(_txt_value(txt("id=abc"), "fn"), "")

    def test_the_key_is_matched_whatever_its_case(self):
        self.assertEqual(_txt_value(txt("FN=Study"), "fn"), "Study")

    def test_an_empty_record_is_handled(self):
        self.assertEqual(_txt_value(b"", "fn"), "")


class ReadNameTests(unittest.TestCase):
    def test_a_pointer_that_loops_does_not_hang(self):
        # A malicious or broken packet must not spin the scanner for ever.
        looping = struct.pack(">HHHHHH", 0, 0, 0, 0, 0, 0) + struct.pack(">H", 0xC000 | 12)
        name, _offset = _read_name(looping, 12)
        self.assertIsInstance(name, str)



class DisplayNameTests(unittest.TestCase):
    """What a device is called in the list when nothing has named it."""

    def test_a_phone_that_says_nothing_is_named_by_its_address(self):
        from mind.network_devices import Device

        # Nine devices all reading "Randomised" is a list nobody can use.
        device = Device(mac="62-4f-00-56-96-09", ip="192.168.18.15", vendor="Randomised")
        self.assertEqual(device.display_name, "Device 15")

    def test_a_real_vendor_is_still_a_better_name_than_an_address(self):
        from mind.network_devices import Device

        device = Device(mac="b8-27-eb-11-22-33", ip="192.168.18.9", vendor="Raspberry Pi")
        self.assertEqual(device.display_name, "Raspberry Pi")

    def test_anything_the_device_said_about_itself_wins(self):
        from mind.network_devices import Device

        device = Device(mac="aa", ip="192.168.18.15", vendor="Randomised", hostname="Adams-iPhone")
        self.assertEqual(device.display_name, "Adams-iPhone")

    def test_a_name_you_typed_wins_over_all_of_it(self):
        from mind.network_devices import Device

        device = Device(mac="aa", ip="192.168.18.15", vendor="Randomised", hostname="android-1234", custom_name="Adam's Phone")
        self.assertEqual(device.display_name, "Adam's Phone")

    def test_with_no_address_at_all_it_says_unknown(self):
        from mind.network_devices import Device

        self.assertEqual(Device(mac="aa").display_name, "Unknown")

if __name__ == "__main__":
    unittest.main()
