import threading
import uuid

from vibeflow.single_instance import SingleInstance


def test_single_instance_flow():
    name = f"VibeFlowTest-{uuid.uuid4().hex}"
    first = SingleInstance(name)
    second = SingleInstance(name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False

        hit = threading.Event()
        first.watch(hit.set)
        second.signal_show()
        assert hit.wait(2.0)
        hit.clear()
        second.signal_show()
        assert hit.wait(2.0)
    finally:
        first.close()
        first.close()
        second.close()
        second.close()


def test_mutex_released_after_close():
    name = f"VibeFlowTest-{uuid.uuid4().hex}"
    a = SingleInstance(name)
    assert a.acquire()
    a.close()
    b = SingleInstance(name)
    try:
        assert b.acquire() is True
    finally:
        b.close()
