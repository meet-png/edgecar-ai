"""SOME/IP (AUTOSAR Scalable service-Oriented MiddlewarE over IP) notifications, captured as pcap.

Header (16 bytes, big-endian): Message ID = Service ID · Method/Event ID, Length (bytes after this field), Request ID
= Client ID · Session ID, Protocol Version (1), Interface Version, Message Type, Return Code. Events are
notifications (message type 0x02) with event IDs ≥ 0x8000.

Captures are written as classic pcap (Ethernet / IPv4 / UDP), so Wireshark opens them directly — enable the SOME/IP
dissector on UDP port 30501 ("Decode As…").

Service catalogue used by EdgeCar (IDs in the private range):

==========================  ======  ======  ===============================================================
Service                     ID      Event   Payload (big-endian)
==========================  ======  ======  ===============================================================
ADAS state                  0x1A01  0x8001  state u8 · cause u8 · speed u16 [0.01 km/h] · value u16 · ambient i8
Thermal management          0x2B01  0x8001  power limit u16 [kW] · hottest cell i16 [0.1 °C] · reason u8
==========================  ======  ======  ===============================================================
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

HEADER = struct.Struct(">HHIHHBBBB")
PROTOCOL_VERSION = 0x01
NOTIFICATION = 0x02
E_OK = 0x00
UDP_PORT = 30501

ADAS_SERVICE, ADAS_STATE_EVENT = 0x1A01, 0x8001
THERMAL_SERVICE, THERMAL_LIMIT_EVENT = 0x2B01, 0x8001

ADAS_PAYLOAD = struct.Struct(">BBHHb")
THERMAL_PAYLOAD = struct.Struct(">HhB")


@dataclass(frozen=True)
class Message:
    service_id: int
    event_id: int
    payload: bytes
    client_id: int = 0x0000
    session_id: int = 0x0001
    interface_version: int = 0x01
    message_type: int = NOTIFICATION
    return_code: int = E_OK


def encode(m: Message) -> bytes:
    return (
        HEADER.pack(
            m.service_id,
            m.event_id,
            len(m.payload) + 8,
            m.client_id,
            m.session_id,
            PROTOCOL_VERSION,
            m.interface_version,
            m.message_type,
            m.return_code,
        )
        + m.payload
    )


def decode(data: bytes) -> Message:
    if len(data) < HEADER.size:
        raise ValueError("SOME/IP message shorter than its header")
    sid, eid, length, cid, sess, proto, iface, mtype, rc = HEADER.unpack_from(data)
    if proto != PROTOCOL_VERSION:
        raise ValueError(f"unsupported SOME/IP protocol version {proto}")
    if length + 8 != len(data):
        raise ValueError(f"length field {length} does not match message size {len(data)}")
    return Message(sid, eid, data[HEADER.size :], cid, sess, iface, mtype, rc)


# --------------------------------------------------------------------------------------------- pcap I/O

_PCAP_GLOBAL = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)  # µs timestamps, Ethernet link type
_ETH = bytes.fromhex("02000000000a02000000000b") + b"\x08\x00"  # dst, src (locally administered), IPv4


def _ipv4(src: str, dst: str, payload_len: int) -> bytes:
    total = 20 + payload_len
    hdr = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        total,
        0,
        0x4000,
        64,
        17,
        0,
        bytes(map(int, src.split("."))),
        bytes(map(int, dst.split("."))),
    )
    s = sum(struct.unpack(">10H", hdr))
    s = (s & 0xFFFF) + (s >> 16)
    s = (s & 0xFFFF) + (s >> 16)
    return hdr[:10] + struct.pack(">H", ~s & 0xFFFF) + hdr[12:]


def write_pcap(
    records: Iterable[tuple[float, bytes]], path: Path, src: str = "192.168.10.20", dst: str = "192.168.10.1"
) -> int:
    """Write ``(posix_ts, someip_bytes)`` records as UDP datagrams (checksum 0 is valid for IPv4)."""
    n = 0
    with open(path, "wb") as fh:
        fh.write(_PCAP_GLOBAL)
        for ts, data in sorted(records, key=lambda r: r[0]):
            udp = struct.pack(">HHHH", UDP_PORT, UDP_PORT, 8 + len(data), 0) + data
            frame = _ETH + _ipv4(src, dst, len(udp)) + udp
            sec = int(ts)
            fh.write(struct.pack("<IIII", sec, int(round((ts - sec) * 1e6)), len(frame), len(frame)) + frame)
            n += 1
    return n


def read_pcap(path: Path) -> Iterator[tuple[float, bytes]]:
    """Yield ``(posix_ts, udp_payload)`` for every UDP datagram on the SOME/IP port."""
    with open(path, "rb") as fh:
        if fh.read(24)[:4] != _PCAP_GLOBAL[:4]:
            raise ValueError("not a microsecond pcap file")
        while hdr := fh.read(16):
            sec, usec, incl, _ = struct.unpack("<IIII", hdr)
            frame = fh.read(incl)
            if frame[12:14] != b"\x08\x00" or frame[23] != 17:
                continue
            ihl = (frame[14] & 0x0F) * 4
            udp = frame[14 + ihl :]
            if struct.unpack(">H", udp[2:4])[0] != UDP_PORT:
                continue
            yield sec + usec / 1e6, udp[8:]
