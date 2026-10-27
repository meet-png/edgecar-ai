"""UDS (ISO 14229-1) diagnostic trouble codes carried over ISO-TP (ISO 15765-2) on classic CAN.

EdgeCar never *sends* diagnostic requests — the AI zone has no transmit path. The vehicle's diagnostic manager
(central gateway) already polls ECUs with ``ReadDTCInformation`` (0x19 / reportDTCByStatusMask 0x02); EdgeCar
listens to the responses and decodes them.

DTC format: 3 bytes = 2-byte SAE J2012 code + failure-type byte. The two high bits of the first byte select the
system letter (P/C/B/U), the next two bits the first digit, then three hex digits.
"""

from __future__ import annotations

from dataclasses import dataclass

SID_READ_DTC = 0x19
SUB_BY_STATUS_MASK = 0x02
POSITIVE = 0x40

# ISO 15765-4 11-bit OBD addressing: request 0x7E0+n, response 0x7E8+n
ECU_ADDRESSES = {"VCU": (0x7E0, 0x7E8), "BMS": (0x7E4, 0x7EC)}

STATUS_BITS = (
    "testFailed",
    "testFailedThisOperationCycle",
    "pendingDTC",
    "confirmedDTC",
    "testNotCompletedSinceLastClear",
    "testFailedSinceLastClear",
    "testNotCompletedThisOperationCycle",
    "warningIndicatorRequested",
)

# SAE J2012 definitions for the codes this system decodes into readable text.
DTC_TEXT = {
    "P0C73": "Motor electronics coolant pump 'A' control",
    "P0A7E": "Hybrid/EV battery pack 'A' over temperature",
    "P0A93": "Inverter 'A' cooling system performance",
}

_LETTERS = "PCBU"


def dtc_to_bytes(code: str, failure_type: int = 0) -> bytes:
    letter, digits = code[0].upper(), code[1:]
    hi = (_LETTERS.index(letter) << 6) | (int(digits[0]) << 4) | int(digits[1], 16)
    return bytes([hi, int(digits[2:4], 16), failure_type])


def bytes_to_dtc(b: bytes) -> tuple[str, int]:
    letter = _LETTERS[b[0] >> 6]
    return f"{letter}{(b[0] >> 4) & 0x3}{b[0] & 0xF:X}{b[1]:02X}", b[2]


def status_flags(status: int) -> list[str]:
    return [name for bit, name in enumerate(STATUS_BITS) if status & (1 << bit)]


@dataclass(frozen=True)
class DtcRecord:
    code: str
    failure_type: int
    status: int


def encode_report(dtcs: list[DtcRecord], availability_mask: int = 0xFF) -> bytes:
    """Positive response 0x59 0x02 to ReadDTCInformation / reportDTCByStatusMask."""
    body = b"".join(dtc_to_bytes(d.code, d.failure_type) + bytes([d.status]) for d in dtcs)
    return bytes([SID_READ_DTC | POSITIVE, SUB_BY_STATUS_MASK, availability_mask]) + body


def decode_report(payload: bytes) -> list[DtcRecord]:
    if len(payload) < 3 or payload[0] != (SID_READ_DTC | POSITIVE) or payload[1] != SUB_BY_STATUS_MASK:
        raise ValueError(f"not a reportDTCByStatusMask response: {payload[:3].hex()}")
    out = []
    for i in range(3, len(payload) - 3, 4):
        code, ftb = bytes_to_dtc(payload[i : i + 3])
        out.append(DtcRecord(code, ftb, payload[i + 3]))
    return out


# --------------------------------------------------------------------------------------------- ISO-TP framing


def isotp_frames(payload: bytes) -> list[bytes]:
    """Segment a UDS payload into 8-byte ISO-TP frames (single frame, or first frame + consecutive frames)."""
    if len(payload) <= 7:
        return [bytes([len(payload)]) + payload + b"\xaa" * (7 - len(payload))]
    n = len(payload)
    frames = [bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6]]
    rest, seq = payload[6:], 1
    while rest:
        chunk, rest = rest[:7], rest[7:]
        frames.append(bytes([0x20 | (seq & 0xF)]) + chunk + b"\xaa" * (7 - len(chunk)))
        seq += 1
    return frames


FLOW_CONTROL_CTS = bytes([0x30, 0x00, 0x00]) + b"\xaa" * 5  # clear-to-send, no block limit, no separation time


class IsoTpReassembler:
    """Rebuilds payloads from a stream of frames on one CAN id (flow-control frames are ignored)."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._need = 0
        self._next = 1

    def feed(self, frame: bytes) -> bytes | None:
        kind = frame[0] >> 4
        if kind == 0:  # single frame
            return bytes(frame[1 : 1 + (frame[0] & 0xF)])
        if kind == 1:  # first frame
            self._need = ((frame[0] & 0xF) << 8) | frame[1]
            self._buf = bytearray(frame[2:8])
            self._next = 1
        elif kind == 2 and self._need:  # consecutive frame
            if frame[0] & 0xF != self._next & 0xF:
                self._buf, self._need = bytearray(), 0  # sequence error: drop the message
                return None
            self._buf += frame[1:8]
            self._next += 1
        if self._need and len(self._buf) >= self._need:
            out, self._need = bytes(self._buf[: self._need]), 0
            return out
        return None
