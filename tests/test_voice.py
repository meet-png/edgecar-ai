import numpy as np

from edgecar import voice


def test_wav_round_trip():
    x = (np.sin(np.linspace(0, 200, 16000)) * 0.5).astype(np.float32)
    y, rate = voice.read_wav(voice.write_wav(x, 16000))
    assert rate == 16000 and len(y) == len(x)
    assert np.max(np.abs(y - x)) < 1e-3


def test_units_are_read_out():
    s = voice.speakable("Energy use rose from 13.3 to 14.1 kWh/100 km (≈ 7 km of range) at −2 °C.")
    assert "kilowatt-hours per hundred kilometres" in s
    assert "about 7 kilometres" in s and "degrees" in s
    assert "kWh" not in s and "(" not in s


def test_app_runs_text_only_without_speech_models(tmp_path):
    assert voice.load(root=tmp_path) is None


def test_damaged_speech_models_fall_back_to_text(tmp_path):
    stt = tmp_path / voice.STT_MODEL
    stt.mkdir()
    (stt / "encoder_model.ort").write_bytes(b"truncated download")
    (tmp_path / voice.TTS_MODEL).mkdir()  # a voice folder without its .onnx
    assert voice.load(root=tmp_path) is None
