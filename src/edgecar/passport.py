"""Battery Health Passport: a signed, privacy-preserving battery report a buyer can verify (ADR-0011).

    edgecar passport --vehicle i3-tum --out build/passport      # report.json, report.svg (QR), verify.html

The report holds conclusions only — capacity, state of health with its interval, the trend, wear exposure, the
period measured — never a trip, a timestamp finer than a day or a location. It is signed with the vehicle's Ed25519
key; the QR code carries the signed report itself, so a buyer's phone can check it with no network and no account.
What the signature proves: these numbers were produced by this vehicle's software, from its own recorded data, and
not changed since. In a production vehicle the key lives in the HSM behind secure boot (docs/ARCHITECTURE.md §8);
this prototype keeps it in a file.
"""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from edgecar import __version__
from edgecar.analytics import battery
from edgecar.store import Store

SCHEMA = "edgecar.battery-passport/1"
QR_PREFIX = "ECBP1:"
# The checker page (docs/verify/index.html, static, no server code). The signed report rides after the "#", which a
# browser never sends to the server, so the check happens on the buyer's phone and the report is not uploaded.
VERIFY_URL = "https://meet-png.github.io/edgecar-ai/verify/"
# A health figure is stated only when the evidence is tight; otherwise the report says it is inconclusive.
MIN_STRETCHES = 8
MAX_HALF_WIDTH_PTS = 3.0  # half-width of the 95 % interval of SoH, in percentage points


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(obj: dict) -> bytes:
    """The bytes that are signed: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# ------------------------------------------------------------------------------------------------------------- keys


def load_or_create_key(path: Path) -> Ed25519PrivateKey:
    path = Path(path)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        assert isinstance(key, Ed25519PrivateKey)
        return key
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    path.chmod(0o600)
    return key


def public_raw(key: Ed25519PrivateKey | Ed25519PublicKey) -> bytes:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id(pub: bytes) -> str:
    return hashlib.sha256(pub).hexdigest()[:16]


# ----------------------------------------------------------------------------------------------------------- report


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(float(x), nd)


def build(store: Store, vehicle_id: str) -> dict:
    """The report's content: conclusions only, rounded to what the evidence supports."""
    v = store.sql("SELECT name FROM vehicles WHERE vehicle_id = ?", [vehicle_id])
    if v.empty:
        raise KeyError(f"unknown vehicle {vehicle_id!r}")
    name = v["name"][0]
    frames = store.sql(
        "SELECT ts, trip_id, soc_pct, batt_current_a, batt_voltage_v, batt_temp_c FROM frames "
        "WHERE vehicle_id = ? ORDER BY ts",
        [vehicle_id],
    )
    charges = store.sql("SELECT detail FROM events WHERE vehicle_id = ? AND category = 'charging'", [vehicle_id])
    charges = pd.DataFrame([json.loads(d) for d in charges["detail"]]) if len(charges) else pd.DataFrame()
    st = battery.stretches(frames)
    h = battery.estimate(st, battery.RATED_AH.get(name))
    half = (h.soh_pct_ci[1] - h.soh_pct_ci[0]) / 2 if h.soh_pct_ci else None
    measured = h.soh_pct is not None and h.n >= MIN_STRETCHES and half is not None and half <= MAX_HALF_WIDTH_PTS
    if measured:
        verdict = "measured"
    elif h.n == 0:
        verdict = "no measurements"
    else:
        verdict = "inconclusive"
    # what was measured, as a digest: lets an auditor holding the same data check the inputs, reveals nothing
    digest = hashlib.sha256(
        canonical({"stretches": [[s.start.isoformat(), _r(s.dsoc_pct, 2), _r(s.charge_ah, 3)] for s in st]})
    ).hexdigest()
    return {
        "schema": SCHEMA,
        "vehicle": {"model": name, "battery_id": None},  # BPAN (India's Battery Pack Aadhaar) once packs carry one
        "period": [h.first.date().isoformat(), h.last.date().isoformat()] if h.first else None,
        "verdict": verdict,
        "health": {
            "measurements": h.n,
            "capacity_ah": _r(h.capacity_ah),
            "capacity_ah_ci95": [_r(x) for x in h.capacity_ah_ci] if h.capacity_ah_ci else None,
            "rated_ah": h.rated_ah,
            "soh_pct": _r(h.soh_pct) if measured else None,
            "soh_pct_ci95": [_r(x) for x in h.soh_pct_ci] if measured else None,
            "trend_pct_per_year": _r(h.trend_pct_per_year) if measured else None,
            "trend_ci95": [_r(x) for x in h.trend_ci] if measured and h.trend_ci else None,
        },
        "wear": battery.wear(frames, charges) if len(frames) else {},
        "method": "coulomb counting over drives using >= 15 % of charge; median, bootstrap 95 % interval",
        # what the interval does not cover: the signature proves where the numbers came from, not their accuracy
        "limits": "interval = spread of this car's measurements; not yet validated against a laboratory capacity test",
        "inputs_sha256": digest,
        "software": f"edgecar-ai {__version__}",
        "issued": date.today().isoformat(),
    }


def sign(report: dict, key: Ed25519PrivateKey) -> dict:
    """The signature covers the report's exact bytes, carried as a string: a verifier checks those bytes and only
    then parses them, so no two JSON libraries ever have to agree on how to write a number."""
    pub, payload = public_raw(key), canonical(report)
    return {"payload": payload.decode(), "key": _b64(pub), "key_id": key_id(pub), "sig": _b64(key.sign(payload))}


def report_of(signed: dict) -> dict:
    return json.loads(signed["payload"])


def verify(signed: dict, trusted_key: bytes | None = None) -> bool:
    """True when the signature matches the report — and, if ``trusted_key`` is given, was made with that key
    (without it, anyone could re-sign an altered report with their own key)."""
    try:
        pub = _unb64(signed["key"])
        if trusted_key is not None and pub != trusted_key:
            return False
        Ed25519PublicKey.from_public_bytes(pub).verify(_unb64(signed["sig"]), signed["payload"].encode())
    except (InvalidSignature, ValueError, KeyError):
        return False
    return True


# --------------------------------------------------------------------------------------------------------------- QR


def to_qr_text(signed: dict, verify_url: str = VERIFY_URL) -> str:
    return f"{verify_url}#{QR_PREFIX}{_b64(zlib.compress(canonical(signed), 9))}"


def from_qr_text(text: str) -> dict:
    i = text.find(QR_PREFIX)
    if i < 0:
        raise ValueError("not an EdgeCar battery passport")
    return json.loads(zlib.decompress(_unb64(text[i + len(QR_PREFIX) :])))


def qr_svg(signed: dict, verify_url: str = VERIFY_URL) -> str:
    import segno

    return segno.make(to_qr_text(signed, verify_url), error="m").svg_inline(scale=4, dark="#0b1220", light="#ffffff")


def sentence(report: dict) -> str:
    """The report in one sentence, for the assistant's answer."""
    h, v = report["health"], report["verdict"]
    if v == "measured":
        trend = ""
        if h["trend_pct_per_year"] is not None:
            lo, hi = h["trend_ci95"]
            trend = (
                f" No clear change over the period ({h['trend_pct_per_year']:+.1f} % a year, interval {lo:+.1f} to "
                f"{hi:+.1f})."
                if lo < 0 < hi
                else f" It is changing by {h['trend_pct_per_year']:+.1f} % a year."
            )
        cap = f"{h['capacity_ah']:.1f} Ah of a rated {h['rated_ah']:.0f} Ah"
        return (
            f"Battery health {h['soh_pct']:.0f} % ({cap}; 95 % interval {h['soh_pct_ci95'][0]:.0f}–"
            f"{h['soh_pct_ci95'][1]:.0f} %), measured on {h['measurements']} drives.{trend}"
        )
    if v == "inconclusive":
        ci = h["capacity_ah_ci95"]
        spread = f" (capacity {ci[0]:.0f}–{ci[1]:.0f} Ah)" if ci else ""
        return (
            f"Not enough consistent evidence to state battery health yet: {h['measurements']} long drives measured"
            f"{spread}, and a health figure needs at least {MIN_STRETCHES} that pin it down to ±"
            f"{MAX_HALF_WIDTH_PTS:.0f} points."
        )
    return "No drive so far used enough charge to measure the battery's capacity."


def _json_default(o):
    if isinstance(o, datetime | date):
        return o.isoformat()
    raise TypeError(type(o))


def write(signed: dict, out: Path) -> list[Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / "report.json", out / "report.svg"]
    paths[0].write_text(json.dumps(signed, indent=2, default=_json_default) + "\n", encoding="utf-8")
    paths[1].write_text(qr_svg(signed), encoding="utf-8")
    return paths
