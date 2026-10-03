import numpy as np

from vibeflow import audio
from vibeflow.audio import Recorder, _levels
from vibeflow.config import AudioConfig


def test_levels_empty_and_silence():
    assert _levels(np.zeros(0, dtype=np.int16)) == (0.0, 0.0)
    assert _levels(np.zeros(100, dtype=np.int16)) == (0.0, 0.0)


def test_levels_values():
    data = np.array([16384, -16384, 16384, -16384], dtype=np.int16)
    peak, rms = _levels(data)
    assert abs(peak - 0.5) < 1e-9
    assert abs(rms - 0.5) < 1e-9


def test_levels_full_scale_negative():
    peak, _ = _levels(np.array([-32768], dtype=np.int16))
    assert peak == 1.0


def test_stop_reports_levels(monkeypatch, tmp_path):
    monkeypatch.setattr(audio.tempfile, "gettempdir", lambda: str(tmp_path))

    class FakeStream:
        def stop(self): pass
        def close(self): pass

    r = Recorder(AudioConfig(sample_rate=16000))
    r._stream = FakeStream()
    r._frames = [np.full((800, 1), 8192, dtype=np.int16), np.full((800, 1), -8192, dtype=np.int16)]
    rec = r.stop()
    assert rec.path.exists()
    assert abs(rec.seconds - 0.1) < 1e-9
    assert abs(rec.peak - 0.25) < 1e-9
    assert abs(rec.rms - 0.25) < 1e-9
    assert not r.is_recording


def test_stop_when_not_recording_has_zero_levels():
    rec = Recorder(AudioConfig()).stop()
    assert (rec.seconds, rec.peak, rec.rms) == (0.0, 0.0, 0.0)
