import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Full

import pytest

from py_sharedmemory import sender as sender_module

from .helpers import assert_unlinked


def test_concurrent_first_puts_and_multiple_consumers(make_pair):
    sender, receiver = make_pair(3)
    start = threading.Barrier(8)

    def produce(producer_id):
        start.wait(timeout=5)
        for index in range(50):
            sender.put((producer_id, index), timeout=5)

    def consume():
        start.wait(timeout=5)
        return [receiver.get(timeout=5) for _ in range(50)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        producers = [pool.submit(produce, index) for index in range(4)]
        consumers = [pool.submit(consume) for _ in range(4)]
        for future in producers:
            future.result(timeout=10)
        received = [item for future in consumers for item in future.result(timeout=10)]
    assert sorted(received) == [(producer, index) for producer in range(4) for index in range(50)]
    assert sender.wait_for_all_ack(timeout=3)
    assert not sender._blocks


def test_capacity_includes_puts_still_serializing(make_pair, monkeypatch):
    sender, receiver = make_pair()
    started = threading.Event()
    release = threading.Event()
    real_convert = sender_module.serialize

    def slow_convert(*args, **kwargs):
        started.set()
        if not release.wait(3):
            raise TimeoutError("Test did not release serialization")
        return real_convert(*args, **kwargs)

    monkeypatch.setattr(sender_module, "serialize", slow_convert)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(sender.put, 1)
        assert started.wait(3)
        try:
            with pytest.raises(Full):
                sender.put_nowait(2)
            assert not sender.wait_for_all_ack(timeout=0.02)
        finally:
            release.set()
        future.result(timeout=3)
    assert receiver.get(timeout=3) == 1
    assert sender.wait_for_all_ack(timeout=3)


def test_close_during_serialization_frees_the_late_allocation(make_pair, monkeypatch):
    sender, _ = make_pair()
    started = threading.Event()
    release = threading.Event()
    names = []
    real_convert = sender_module.serialize

    def slow_convert(*args, **kwargs):
        started.set()
        if not release.wait(3):
            raise TimeoutError("Test did not release serialization")
        handle, info = real_convert(*args, **kwargs)
        names.append(handle.name)
        return handle, info

    monkeypatch.setattr(sender_module, "serialize", slow_convert)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(sender.put, 1)
        assert started.wait(3)
        try:
            sender.close()
        finally:
            release.set()
        with pytest.raises(BrokenPipeError):
            future.result(timeout=3)
    assert names
    for name in names:
        assert_unlinked(name)


def test_close_wakes_blocked_put_and_drain_waiters(make_pair, monkeypatch):
    sender, _ = make_pair()
    sender.put(1)
    both_waiting = threading.Event()
    wait_count = 0
    real_wait = sender._condition.wait

    def track_wait(timeout=None):
        nonlocal wait_count
        wait_count += 1
        if wait_count == 2:
            both_waiting.set()
        return real_wait(timeout)

    monkeypatch.setattr(sender._condition, "wait", track_wait)

    with ThreadPoolExecutor(max_workers=2) as pool:
        put_future = pool.submit(sender.put, 2)
        wait_future = pool.submit(sender.wait_for_all_ack)
        assert both_waiting.wait(3)
        sender.close()
        for future in (put_future, wait_future):
            with pytest.raises(BrokenPipeError):
                future.result(timeout=3)
