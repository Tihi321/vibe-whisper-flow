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


# ---------------------------------------------------------------- VWT-06: devices, monitor, play

import threading  # noqa: E402
import types  # noqa: E402

import pytest  # noqa: E402

from vibeflow.audio import AudioError, InputMonitor  # noqa: E402

DEVICES = [
    {"name": "Microsoft Sound Mapper - Input", "hostapi": 0, "max_input_channels": 2},
    {"name": "FrontMic (Realtek(R) Audio)", "hostapi": 0, "max_input_channels": 2},
    {"name": "Microphone (Realtek(R) Audio)", "hostapi": 0, "max_input_channels": 2},
    {"name": "Speakers (Realtek(R) Audio)", "hostapi": 0, "max_input_channels": 0},
    {"name": "Primary Sound Capture Driver", "hostapi": 1, "max_input_channels": 2},
    {"name": "Microphone (Realtek(R) Audio)", "hostapi": 1, "max_input_channels": 2},
    {"name": "Microphone (Realtek(R) Audio)", "hostapi": 2, "max_input_channels": 2},
    {"name": "Legacy Wasapi Only Mic", "hostapi": 2, "max_input_channels": 1},
]


class FakeStream:
    def __init__(self, sd, kind, kwargs):
        self.sd, self.kind, self.kwargs = sd, kind, kwargs
        self.callback = kwargs.get("callback")
        self.calls: list = []
        self.written: list = []
        sd.streams.append(self)

    def start(self):
        if self.sd.fail_start:
            raise RuntimeError("boom")
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")

    def write(self, chunk):
        if self.sd.fail_write:
            raise RuntimeError("write failed")
        self.written.append(len(chunk))
        if self.sd.on_write:
            self.sd.on_write()


class FakeSD:
    def __init__(self):
        self.devices = list(DEVICES)
        self.default_idx = 1
        self.fail_query = False
        self.default_query_fails = False
        self.calls: list = []
        self.owned: list = []
        self.streams: list = []
        self.fail_start = False
        self.fail_write = False
        self.on_write = None
        self.default = types.SimpleNamespace(device=[1, 0])

    def query_devices(self, device=None, kind=None):
        self.owned.append(audio._PA_LOCK._is_owned())
        if self.fail_query:
            raise RuntimeError("no portaudio")
        if kind == "input":
            if self.default_query_fails:
                raise RuntimeError("no default")
            d = dict(self.devices[self.default_idx])
            d["index"] = self.default_idx
            return d
        return self.devices

    def _terminate(self):
        self.owned.append(audio._PA_LOCK._is_owned())
        self.calls.append("terminate")

    def _initialize(self):
        self.calls.append("initialize")

    def InputStream(self, **kw):
        self.owned.append(audio._PA_LOCK._is_owned())
        return FakeStream(self, "in", kw)

    def OutputStream(self, **kw):
        self.owned.append(audio._PA_LOCK._is_owned())
        return FakeStream(self, "out", kw)


@pytest.fixture
def fake(monkeypatch):
    sd = FakeSD()
    monkeypatch.setattr(audio, "_sd", lambda: sd)
    monkeypatch.setattr(audio, "_open_streams", 0)
    return sd


def test_input_devices_default_host_api_only(fake):
    devs = audio.input_devices()
    assert [d.name for d in devs] == ["FrontMic (Realtek(R) Audio)", "Microphone (Realtek(R) Audio)"]
    assert [d.index for d in devs] == [1, 2]
    assert [d.is_default for d in devs] == [True, False]
    assert audio.list_input_devices() == [(1, "FrontMic (Realtek(R) Audio)"),
                                          (2, "Microphone (Realtek(R) Audio)")]
    assert all(fake.owned)


def test_input_devices_dedup_and_mapper_removed(fake):
    fake.devices.insert(3, {"name": "frontmic (realtek(r) audio)", "hostapi": 0,
                            "max_input_channels": 2})
    names = [d.name for d in audio.input_devices()]
    assert names.count("FrontMic (Realtek(R) Audio)") == 1
    assert len(names) == 2


def test_input_devices_all_hostapis_when_default_query_fails(fake):
    fake.default_query_fails = True
    devs = audio.input_devices()
    assert "Legacy Wasapi Only Mic" in [d.name for d in devs]
    assert "Microsoft Sound Mapper - Input" not in [d.name for d in devs]
    assert not any(d.is_default for d in devs)


def test_input_devices_error(fake):
    fake.fail_query = True
    with pytest.raises(AudioError):
        audio.input_devices()


def test_default_input_name(fake):
    assert audio.default_input_name() == "FrontMic (Realtek(R) Audio)"
    assert all(fake.owned)
    fake.default_query_fails = True
    assert audio.default_input_name() is None


def test_match_device():
    names = ["Microphone (USB)", "Microphone"]
    assert audio.match_device("", names) is None
    assert audio.match_device("microphone", names) == "Microphone"             # exact beats substring
    assert audio.match_device("MICROPHONE (usb)", names) == "Microphone (USB)"  # case-insensitive
    assert audio.match_device("usb", names) == "Microphone (USB)"              # substring
    assert audio.match_device("nothing", names) is None
    full = "Microphone (High Definition Audio Device)"
    trunc = full[:31]
    assert len(trunc) == 31
    assert audio.match_device(full, [trunc]) == trunc


def test_resolve_device(fake):
    assert audio.resolve_device("Microphone (Realtek(R) Audio)") == 2
    assert audio.resolve_device("") == 0                    # mapper
    assert audio.resolve_device("Legacy Wasapi") == 7        # legacy: any host API
    with pytest.raises(AudioError, match="is not connected"):
        audio.resolve_device("Ghost Mic")
    assert fake.calls.count("terminate") == 1                # one refresh before giving up
    fake.calls.clear()
    assert audio.resolve_device("Ghost Mic", fallback=True) == 0
    assert fake.calls.count("terminate") == 1


def test_resolve_empty_without_mapper(fake):
    fake.devices = [d for d in fake.devices if "Mapper" not in d["name"]]
    fake.default_idx = 0
    assert audio.resolve_device("") is None


def test_refresh_devices(fake):
    assert audio.refresh_devices() is True
    assert fake.calls == ["terminate", "initialize"]
    assert all(fake.owned)
    fake.calls.clear()
    audio._stream_opened()
    try:
        assert audio.refresh_devices() is False
        assert fake.calls == []
    finally:
        audio._stream_closed()


def test_refresh_devices_error(fake):
    def boom():
        raise RuntimeError("x")
    fake._terminate = boom
    with pytest.raises(AudioError, match="Could not refresh"):
        audio.refresh_devices()


def test_recorder_counter_and_lock(fake):
    r = Recorder(AudioConfig(device="Microphone (Realtek(R) Audio)"))
    r.start()
    assert audio.open_stream_count() == 1
    assert fake.streams[0].kwargs["device"] == 2
    assert all(fake.owned)  # every PortAudio call held _PA_LOCK
    assert r.fell_back is False
    r.discard()
    assert audio.open_stream_count() == 0


def test_recorder_start_fallback(fake):
    r = Recorder(AudioConfig(device="Ghost Mic"))
    r.start()
    assert r.fell_back is True
    assert fake.streams[0].kwargs["device"] == 0
    r.discard()
    r2 = Recorder(AudioConfig(device=""))
    r2.start()
    assert r2.fell_back is False
    r2.discard()
    assert audio.open_stream_count() == 0


def test_recorder_resolve_and_open_share_one_lock_hold(fake):
    seen = {}
    started = threading.Event()
    done = threading.Event()

    def other():
        started.set()
        seen["ok"] = audio.refresh_devices()
        done.set()

    orig = fake.InputStream

    def slow_open(**kw):
        t = threading.Thread(target=other)
        t.start()
        started.wait(2)
        assert not done.wait(0.1)  # blocked on the lock between resolve and open
        s = orig(**kw)
        seen["thread"] = t
        return s

    fake.InputStream = slow_open
    r = Recorder(AudioConfig(device="Microphone (Realtek(R) Audio)"))
    r.start()
    seen["thread"].join(2)
    assert seen["ok"] is False  # the stream was open by the time the refresh got the lock
    assert "terminate" not in fake.calls
    r.discard()


def test_recorder_open_failure_keeps_counter_zero(fake):
    fake.fail_start = True
    with pytest.raises(AudioError):
        Recorder(AudioConfig()).start()
    assert audio.open_stream_count() == 0
    assert fake.streams[0].calls == ["close"]


def test_input_monitor_levels_and_capture(fake):
    m = InputMonitor("")
    m.start()
    assert m.running and audio.open_stream_count() == 1
    assert m.device_label == "Windows default (FrontMic (Realtek(R) Audio))"
    cb = fake.streams[0].callback
    cb(np.full((800, 1), 8192, dtype=np.int16), 800, None, None)
    peak, rms, age = m.levels()
    assert abs(peak - 0.25) < 1e-9 and abs(rms - 0.25) < 1e-9 and age >= 0
    cb(np.full((10, 1), 1, dtype=np.int16), 10, None, None)   # before capture: not kept
    m.begin_capture()
    cb(np.full((100, 1), 5, dtype=np.int16), 100, None, None)
    cb(np.full((50, 1), 6, dtype=np.int16), 50, None, None)
    data = m.end_capture()
    cb(np.full((10, 1), 7, dtype=np.int16), 10, None, None)   # after capture: not kept
    assert data.shape == (150,) and data.dtype == np.int16
    assert list(data[:2]) == [5, 5] and list(data[-2:]) == [6, 6]
    assert len(m.end_capture()) == 0
    m.stop()
    m.stop()  # idempotent
    assert not m.running and audio.open_stream_count() == 0
    assert all(fake.owned)


def test_input_monitor_named_device_and_not_connected(fake):
    m = InputMonitor("Microphone (Realtek(R) Audio)")
    m.start()
    assert fake.streams[0].kwargs["device"] == 2
    m.stop()
    with pytest.raises(AudioError, match="is not connected"):
        InputMonitor("Ghost").start()
    assert audio.open_stream_count() == 0


def test_input_monitor_open_failure(fake):
    fake.fail_start = True
    with pytest.raises(AudioError, match="exclusive mode"):
        InputMonitor("").start()
    assert audio.open_stream_count() == 0


def test_play_chunks_and_counter(fake):
    audio.play(np.zeros(1600 * 2 + 300, dtype=np.int16), 16000)
    s = fake.streams[0]
    assert s.written == [1600, 1600, 300]
    assert s.calls == ["start", "stop", "close"]
    assert audio.open_stream_count() == 0


def test_play_cancel_stops_early(fake):
    cancel = threading.Event()
    fake.on_write = cancel.set
    audio.play(np.zeros(16000, dtype=np.int16), 16000, cancel)
    assert fake.streams[0].written == [1600]
    assert audio.open_stream_count() == 0


def test_play_error_closes_stream(fake):
    fake.fail_write = True
    with pytest.raises(AudioError, match="Could not play back"):
        audio.play(np.zeros(1600, dtype=np.int16), 16000)
    assert "close" in fake.streams[0].calls
    assert audio.open_stream_count() == 0


def test_write_wav_round_trip(tmp_path):
    import wave

    data = np.arange(-5, 5, dtype=np.int16)
    p = tmp_path / "sub" / "a.wav"
    audio.write_wav(p, data, 16000)
    with wave.open(str(p)) as wf:
        assert wf.getframerate() == 16000 and wf.getnchannels() == 1
        got = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    assert list(got) == list(data)


def test_level_helpers():
    assert audio.level_db(0.0) == -90.0
    assert abs(audio.level_db(1.0)) < 1e-9
    assert abs(audio.level_db(0.1) + 20.0) < 1e-9
    assert audio.meter_percent(0.0) == 0
    assert audio.meter_percent(1e-3) == 0
    assert audio.meter_percent(1.0) == 100
    assert audio.meter_percent(10 ** (-30 / 20)) == 50
    assert audio.level_verdict(0.0, 0.01, 1.4) == "quiet"
    assert audio.level_verdict(0.0, 0.01, 1.5) == "silent"
    assert audio.level_verdict(0.005, 0.01) == "quiet"
    assert audio.level_verdict(0.01, 0.01) == "ok"
    assert audio.level_verdict(0.98, 0.01) == "ok"
    assert audio.level_verdict(0.99, 0.01) == "clipping"
