"""Who else is on this network.

Three ways of asking, because no single one sees everything. A ping sweep wakes
devices that would otherwise be silent and fills the ARP table, which is where
the MAC addresses come from. mDNS asks devices to say their own name, which is
the only way to learn that 192.168.18.5 calls itself Android_0DHJR - and it also
finds devices that ignore pings entirely, as a Chromecast here does. Reverse DNS
covers whatever the router happens to know.

Nothing here needs the router's password, and nothing is sent anywhere off the
network. A sweep does put a packet on every address in the subnet, which is
harmless at home and best left off on a network someone else runs.

The reading and the remembering are kept apart: scanning returns what was seen
just now, and merge() folds that into what was known before. Only the second is
where the awkward questions live - when a device counts as new, what happens
when its address changes - so only the second needs to be tested, and it can be
tested without a network.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any


MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
# Enough threads that a sweep of 254 addresses takes a second or two, few enough
# that it does not look like a flood.
SWEEP_WORKERS = 64
PING_TIMEOUT_MS = 400
UNKNOWN = "Unknown"

# Address prefixes, three bytes of the MAC, for the makers most likely to be on
# a home network. A full IEEE list is 35,000 entries and about 1.5 MB; this is
# the short version, and anything not in it reads as Unknown rather than wrong.
OUI_VENDORS: dict[str, str] = {
    "00-05-cd": "Denon", "00-0c-29": "VMware", "00-13-a9": "Sony",
    "00-15-99": "Samsung", "00-16-6c": "Samsung", "00-17-88": "Philips Hue",
    "00-1a-11": "Google", "00-1b-63": "Apple", "00-1d-0f": "TP-Link",
    "00-1e-c2": "Apple", "00-21-6a": "Intel", "00-23-76": "HTC",
    "00-24-e4": "Withings", "00-26-bb": "Apple", "00-50-56": "VMware",
    "08-00-27": "VirtualBox", "0c-47-c9": "Amazon", "10-9a-dd": "Apple",
    "18-b4-30": "Nest", "1c-1b-0d": "Gigabyte", "24-4b-fe": "ASUS",
    "28-6c-07": "Xiaomi", "2c-f0-5d": "Micro-Star", "30-ae-a4": "Espressif",
    "34-12-98": "Apple", "3c-5a-b4": "Google", "40-b0-76": "ASUS",
    "44-65-0d": "Amazon", "48-e1-e9": "Chongqing", "4c-11-ae": "Espressif",
    "50-c7-bf": "TP-Link", "54-27-1e": "AzureWave", "5c-cf-7f": "Espressif",
    "60-01-94": "Espressif", "64-16-66": "Nest", "68-c6-3a": "Espressif",
    "6c-ad-f8": "Amazon", "70-4d-7b": "ASUS", "74-da-88": "TP-Link",
    "78-11-dc": "Xiaomi", "7c-2e-bd": "Google", "80-7d-3a": "Espressif",
    "84-0d-8e": "Espressif", "88-66-5a": "Apple", "8c-85-90": "Apple",
    "90-e2-ba": "Intel", "94-e9-79": "Google", "98-5d-ad": "Sonos",
    "9c-b6-d0": "Rivet", "a4-77-33": "Google", "ac-63-be": "Amazon",
    "b0-be-76": "TP-Link", "b4-61-42": "Realme", "b8-27-eb": "Raspberry Pi",
    "bc-d0-74": "Xiaomi", "c0-ee-fb": "OnePlus", "c8-3a-35": "Tenda",
    "cc-32-e5": "Xiaomi", "d0-37-45": "TP-Link", "d8-3a-dd": "Raspberry Pi",
    "dc-a6-32": "Raspberry Pi", "e4-5f-01": "Raspberry Pi", "e8-de-27": "TP-Link",
    "ec-fa-bc": "Espressif", "f0-9f-c2": "Ubiquiti", "f4-f5-d8": "Google",
    "f8-e4-3b": "Awair", "fc-ec-da": "Ubiquiti",
}


@dataclass(frozen=True)
class Device:
    """One machine on the network, as it is remembered between scans."""

    mac: str
    ip: str = ""
    hostname: str = ""
    vendor: str = ""
    # What the user called it. Always wins over anything discovered.
    custom_name: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    online: bool = False

    @property
    def display_name(self) -> str:
        return self.custom_name or self.hostname or self.vendor or UNKNOWN

    def seen_label(self, now: float) -> str:
        """How long ago, in words, because a timestamp reads as noise in a list."""
        if self.online:
            return "now"
        gap = max(0.0, now - self.last_seen)
        if gap < 90:
            return "a moment ago"
        if gap < 3600:
            return f"{int(gap // 60)} min ago"
        if gap < 86400:
            hours = int(gap // 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = int(gap // 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"


@dataclass(frozen=True)
class Observation:
    """What a single scan saw, before it is folded into what was known."""

    mac: str
    ip: str = ""
    hostname: str = ""


def is_randomised(mac: str) -> bool:
    """Whether the address is a private one the device made up.

    Phones invent a MAC per network now, so there is no manufacturer to look up.
    The second character carries the bit that says so, and knowing this is why a
    phone can read as "Randomised" rather than the flat "Unknown" that suggests
    something failed.
    """
    parts = (mac or "").split("-")
    if len(parts) != 6 or len(parts[0]) != 2:
        return False
    try:
        return bool(int(parts[0], 16) & 0b10)
    except ValueError:
        return False


def vendor_for(mac: str) -> str:
    known = OUI_VENDORS.get((mac or "").lower()[:8], "")
    if known:
        return known
    return "Randomised" if is_randomised(mac) else ""


# -- reading the network ---------------------------------------------------

_iphlpapi = ctypes.windll.iphlpapi if hasattr(ctypes, "windll") else None
if _iphlpapi is not None:
    # Declared explicitly. Without this the handle comes back truncated to 32
    # bits on a 64-bit build, and closing it writes through a bogus pointer.
    _iphlpapi.IcmpCreateFile.restype = wt.HANDLE
    _iphlpapi.IcmpCloseHandle.argtypes = [wt.HANDLE]
    _iphlpapi.IcmpSendEcho.argtypes = [
        wt.HANDLE,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_ushort,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wt.DWORD,
        wt.DWORD,
    ]
    _iphlpapi.IcmpSendEcho.restype = wt.DWORD


def local_ipv4() -> str:
    """This PC's address on the network it would use to reach the outside.

    Connecting a UDP socket sends nothing; it only asks Windows which interface
    would be used, which is exactly the question being asked.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return str(probe.getsockname()[0])
    except OSError:
        return ""
    finally:
        probe.close()


def subnet_addresses(local_ip: str) -> list[str]:
    """Every address in this /24 except this PC and the broadcast."""
    if not local_ip or local_ip.count(".") != 3:
        return []
    prefix = local_ip.rsplit(".", 1)[0]
    return [f"{prefix}.{host}" for host in range(1, 255) if f"{prefix}.{host}" != local_ip]


def icmp_ping(ip: str, timeout_ms: int = PING_TIMEOUT_MS) -> bool:
    if _iphlpapi is None:
        return False
    handle = _iphlpapi.IcmpCreateFile()
    if not handle or handle == wt.HANDLE(-1).value:
        return False
    try:
        payload = b"mind"
        reply = ctypes.create_string_buffer(192)
        destination = struct.unpack("<I", socket.inet_aton(ip))[0]
        replies = _iphlpapi.IcmpSendEcho(
            handle, destination, payload, len(payload), None, reply, len(reply), timeout_ms
        )
        return bool(replies)
    except (OSError, struct.error):
        return False
    finally:
        _iphlpapi.IcmpCloseHandle(handle)


def ping_sweep(addresses: list[str], workers: int = SWEEP_WORKERS) -> set[str]:
    """Ping the whole subnet at once, to wake it and fill the ARP table."""
    if not addresses:
        return set()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        answered = pool.map(icmp_ping, addresses)
        return {ip for ip, alive in zip(addresses, answered) if alive}


def reverse_dns(ip: str) -> str:
    """Whatever name the network knows for an address, or nothing."""
    try:
        return str(socket.gethostbyaddr(ip)[0]).split(".")[0]
    except (OSError, socket.herror, socket.gaierror):
        return ""


def reverse_dns_all(ips: list[str], budget: float = 2.0) -> dict[str, str]:
    """Look up every address at once, and give up on the whole lot together.

    gethostbyaddr cannot be given a timeout - the socket default does not bind
    it on Windows - and a router that simply never answers takes about five
    seconds per address. Done one after another for nine devices that was
    three quarters of a minute, which is not a scan, it is a hang. They run
    together instead, and whatever has not answered inside the budget is left
    without a name until the next scan.
    """
    if not ips:
        return {}
    found: dict[str, str] = {}
    # Not a context manager: leaving the block would wait for the stragglers,
    # which is the delay being avoided.
    pool = ThreadPoolExecutor(max_workers=min(24, len(ips)))
    try:
        pending = {pool.submit(reverse_dns, ip): ip for ip in ips}
        deadline = time.monotonic() + budget
        for future, ip in pending.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                name = future.result(timeout=remaining)
            except Exception:
                continue
            if name:
                found[ip] = name
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return found


def _dns_question(name: str, qtype: int = 12) -> bytes:
    body = b""
    for label in name.split("."):
        body += bytes([len(label)]) + label.encode("utf-8", "ignore")
    body += b"\x00" + struct.pack(">HH", qtype, 1)
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + body


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    """Read one DNS name, following the compression pointers it may use."""
    labels: list[str] = []
    jumped = False
    end = offset
    for _ in range(64):  # a hard stop, so a malformed packet cannot loop
        if offset >= len(data):
            break
        length = data[offset]
        if length == 0:
            offset += 1
            if not jumped:
                end = offset
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                break
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                end = offset + 2
            offset = pointer
            jumped = True
            continue
        labels.append(data[offset + 1 : offset + 1 + length].decode("utf-8", "replace"))
        offset += 1 + length
        if not jumped:
            end = offset
    return ".".join(labels), end


def mdns_names(local_ip: str, targets: list[str], listen_seconds: float = 3.5) -> dict[str, str]:
    """Ask the network's devices to say their own names.

    Bound to 5353 and joined to the multicast group, because a one-shot query
    from an ephemeral port hears nothing back - the replies go to the group. Any
    name a device offers is attributed to the address it came from, which is the
    only association that is reliably available.
    """
    if not local_ip:
        return {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    names: dict[str, str] = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", MDNS_PORT))
        membership = struct.pack(
            "4s4s", socket.inet_aton(MDNS_GROUP), socket.inet_aton(local_ip)
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.settimeout(0.5)
    except OSError:
        # Another responder already owns the port, or there is no route to the
        # group. Names simply come from elsewhere.
        sock.close()
        return {}

    try:
        for address in targets[:64]:
            reverse = ".".join(reversed(address.split("."))) + ".in-addr.arpa"
            try:
                sock.sendto(_dns_question(reverse), (MDNS_GROUP, MDNS_PORT))
            except OSError:
                continue
        try:
            sock.sendto(
                _dns_question("_services._dns-sd._udp.local"), (MDNS_GROUP, MDNS_PORT)
            )
        except OSError:
            pass

        deadline = time.monotonic() + listen_seconds
        while time.monotonic() < deadline:
            try:
                data, sender = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            name = _first_local_name(data)
            if name and sender[0] not in names:
                names[sender[0]] = name
    finally:
        sock.close()
    return names


def _first_local_name(data: bytes) -> str:
    """The device's own name out of an mDNS reply, if it gave one.

    Answers are read rather than guessed at, but only far enough to find a name
    ending in .local - which is the device calling itself something - and the
    service suffixes are stripped so "Android_0DHJR._googlecast._tcp.local"
    reads as "Android_0DHJR".
    """
    if len(data) < 12:
        return ""
    questions, answers = struct.unpack(">HH", data[4:8])
    offset = 12
    try:
        for _ in range(questions):
            _, offset = _read_name(data, offset)
            offset += 4
        for _ in range(min(answers, 16)):
            owner, offset = _read_name(data, offset)
            if offset + 10 > len(data):
                break
            _rtype, _rclass, _ttl, length = struct.unpack(">HHIH", data[offset : offset + 10])
            offset += 10
            candidate = owner
            if not candidate.endswith(".local") and _rtype == 12:
                candidate, _ = _read_name(data, offset)
            offset += length
            cleaned = _clean_mdns_name(candidate)
            if cleaned:
                return cleaned
    except (struct.error, IndexError):
        return ""
    return ""


def _clean_mdns_name(name: str) -> str:
    if not name.endswith(".local"):
        return ""
    label = name[: -len(".local")]
    # Drop the service part of "device._googlecast._tcp".
    for marker in ("._tcp", "._udp", "._sub"):
        if marker in label:
            label = label.split(marker)[0]
    label = label.split(".")[0].strip()
    if not label or label.startswith("_"):
        return ""
    return label


def scan(
    arp_reader,
    sweep: bool = True,
    use_mdns: bool = True,
    resolve_names: bool = True,
) -> list[Observation]:
    """Look at the network once and report what is there.

    ``arp_reader`` is passed in rather than imported so a test can run the whole
    scan without a network.
    """
    local_ip = local_ipv4()
    addresses = subnet_addresses(local_ip)
    alive: set[str] = set()
    if sweep:
        alive = ping_sweep(addresses)
    table = arp_reader() or {}

    names: dict[str, str] = {}
    if use_mdns:
        # Asked of everything with a MAC and everything that answered a ping:
        # a device may ignore pings and still announce itself, and another may
        # answer pings without ever reaching the ARP table.
        names.update(mdns_names(local_ip, sorted(set(table.values()) | alive)))

    if resolve_names:
        missing = [ip for mac, ip in table.items() if ip and ip not in names]
        names.update({ip: name for ip, name in reverse_dns_all(missing).items()})

    observations: list[Observation] = []
    for mac, ip in table.items():
        observations.append(Observation(mac=mac, ip=ip, hostname=names.get(ip, "")))
    return observations


# -- remembering -----------------------------------------------------------


def merge(
    known: list[Device],
    observed: list[Observation],
    now: float,
    online_grace: float = 0.0,
) -> tuple[list[Device], list[Device]]:
    """Fold a scan into what was already known.

    Returns every device and the ones seen for the first time. A device is
    identified by its MAC, so a router handing it a new address tomorrow does
    not make it a stranger; a name the user typed is never overwritten by a name
    the network offered.
    """
    by_mac = {device.mac: device for device in known}
    seen_now = {observation.mac for observation in observed}
    arrivals: list[Device] = []
    result: list[Device] = []

    for observation in observed:
        existing = by_mac.get(observation.mac)
        if existing is None:
            device = Device(
                mac=observation.mac,
                ip=observation.ip,
                hostname=observation.hostname,
                vendor=vendor_for(observation.mac),
                first_seen=now,
                last_seen=now,
                online=True,
            )
            arrivals.append(device)
        else:
            device = replace(
                existing,
                ip=observation.ip or existing.ip,
                # A name already found is kept when a scan comes back empty
                # handed, which mDNS often does for a device that was busy.
                hostname=observation.hostname or existing.hostname,
                vendor=existing.vendor or vendor_for(observation.mac),
                last_seen=now,
                online=True,
            )
        result.append(device)

    for device in known:
        if device.mac in seen_now:
            continue
        still_online = bool(online_grace) and (now - device.last_seen) <= online_grace
        result.append(replace(device, online=still_online))

    result.sort(key=lambda device: (not device.online, device.display_name.lower()))
    return result, arrivals


def rename(devices: list[Device], mac: str, name: str) -> list[Device]:
    """Give a device the name its owner calls it."""
    cleaned = (name or "").strip()[:60]
    return [
        replace(device, custom_name=cleaned) if device.mac == mac else device
        for device in devices
    ]


def to_dict(device: Device) -> dict[str, Any]:
    return {
        "mac": device.mac,
        "ip": device.ip,
        "hostname": device.hostname,
        "vendor": device.vendor,
        "custom_name": device.custom_name,
        "first_seen": device.first_seen,
        "last_seen": device.last_seen,
    }


def from_dict(payload: Any) -> Device | None:
    if not isinstance(payload, dict):
        return None
    mac = str(payload.get("mac", "")).strip().lower()
    if not mac:
        return None
    try:
        first_seen = float(payload.get("first_seen", 0) or 0)
        last_seen = float(payload.get("last_seen", 0) or 0)
    except (TypeError, ValueError):
        first_seen = last_seen = 0.0
    return Device(
        mac=mac,
        ip=str(payload.get("ip", "")),
        hostname=str(payload.get("hostname", "")),
        vendor=str(payload.get("vendor", "")),
        custom_name=str(payload.get("custom_name", "")),
        first_seen=first_seen,
        last_seen=last_seen,
        # Nothing is online until a scan says so; a saved file is only history.
        online=False,
    )
