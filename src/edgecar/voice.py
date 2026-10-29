"""On-device speech: push-to-talk recognition and spoken replies, both offline.

* Recognition — Moonshine Tiny (MIT; 27 M parameters, 8-bit, 28 MB), built for short commands on small CPUs.
* Speech — a Piper voice (VITS, 8-bit, 19 MB) trained from scratch on public-domain LibriVox recordings.

Both run through sherpa-onnx (Apache-2.0) on the CPU. Optional: ``pip install -e .[voice]`` and
``python -m edgecar.prep.fetch voice``; without them the app is text-only.

Licence note for a production build: Piper's phonemiser is espeak-ng (GPL-3.0). That is fine for this demo; a
vehicle image would need a GPL-free grapheme-to-phoneme step or a commercially licensed voice.
"""

from __future__ import annotations

import io
import re
import wave
from pathlib import Path

import numpy as np

ROOT = Path("models/voice")
STT_MODEL = "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27"
TTS_MODEL = "vits-piper-en_US-kristin-medium-int8"
RATE = 16_000  # what the recogniser expects; the page resamples the microphone to this
MAX_SECONDS = 9.0  # Moonshine handles up to ~9.5 s per pass; a spoken question is far shorter


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """16-bit PCM WAV → float32 samples in [-1, 1] (first channel) and the sample rate."""
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM")
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        rate, channels = w.getframerate(), w.getnchannels()
    return (frames.reshape(-1, channels)[:, 0].astype(np.float32) / 32768.0), rate


def write_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def speakable(text: str) -> str:
    """Units and symbols as they should be read aloud."""
    for pattern, spoken in [
        (r"kWh/100 km", "kilowatt-hours per hundred kilometres"),
        (r"km/kWh", "kilometres per kilowatt-hour"),
        (r"L/100 km", "litres per hundred kilometres"),
        (r"km/h", "kilometres an hour"),
        (r"°C", " degrees"),
        (r"\bkWh\b", "kilowatt-hours"),
        (r"\bkW\b", "kilowatts"),
        (r"\bkm\b", "kilometres"),
        (r"≈ ?", "about "),
        (r" — ", ", "),
        (r"\bADAS\b", "driver assistance"),
        (r"\bDTC\b", "fault code"),
    ]:
        text = re.sub(pattern, spoken, text)
    return re.sub(r"\(([^)]*)\)", r", \1,", text)


class Voice:
    def __init__(self, root: Path = ROOT, threads: int = 2) -> None:
        import sherpa_onnx

        self.can_listen = self.can_speak = False
        stt = root / STT_MODEL
        if stt.exists():
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
                encoder=str(stt / "encoder_model.ort"),
                decoder=str(stt / "decoder_model_merged.ort"),
                tokens=str(stt / "tokens.txt"),
                num_threads=threads,
            )
            self.can_listen = True
        tts = root / TTS_MODEL
        onnx = next(tts.glob("*.onnx"), None) if tts.exists() else None
        if onnx is not None:
            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=str(onnx), tokens=str(tts / "tokens.txt"), data_dir=str(tts / "espeak-ng-data")
                    ),
                    num_threads=threads,
                )
            )
            self.tts = sherpa_onnx.OfflineTts(config)
            self.tts.generate("Ready.", sid=0, speed=1.0)  # the first synthesis is slow; pay for it at start-up
            self.can_speak = True

    def transcribe(self, wav_bytes: bytes) -> str:
        samples, rate = read_wav(wav_bytes)
        samples = samples[: int(MAX_SECONDS * rate)]
        stream = self.recognizer.create_stream()
        stream.accept_waveform(rate, samples)
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()

    def speak(self, text: str) -> bytes:
        audio = self.tts.generate(speakable(text), sid=0, speed=1.0)
        return write_wav(np.asarray(audio.samples, dtype=np.float32), audio.sample_rate)


def load(root: Path = ROOT, threads: int = 2) -> Voice | None:
    """The speech engine, or ``None`` when sherpa-onnx or the models are not installed."""
    try:
        v = Voice(root, threads)
    except ImportError:
        return None
    except Exception as e:  # a damaged or partial download: the app still works, by text
        print(f"voice: off — could not load the speech models in {root} ({type(e).__name__}: {e})")
        return None
    return v if (v.can_listen or v.can_speak) else None
