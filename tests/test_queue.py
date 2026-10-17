import multiprocessing as mp
import os
import pickle
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from multiprocessing.shared_memory import SharedMemory
from queue import Empty, Full
from unittest.mock import Mock

import numpy as np
import pytest

from py_sharedmemory import SharedMemoryReceiver, SharedMemorySender, create_shared_memory_pair
from py_sharedmemory import sender as sender_module
from py_sharedmemory.convert import release_shared_memory, serialize

from .helpers import LocalQueue, assert_unlinked


@pytest.mark.parametrize("capacity", [-10, -1, 1.5, "1", True, False])
def test_invalid_capacity_fails_before_resources_are_created(capacity):
    ctx = Mock()
    error = ValueError if type(capacity) is int else TypeError
    with pytest.raises(error, match="capacity"):
        create_shared_memory_pair(capacity, ctx=ctx)
    ctx.Value.assert_not_called()
    ctx.Queue.assert_not_called()


def test_factory_queue_failure_closes_first_queue():
    ctx = Mock()
    data_queue = Mock()
    ctx.Queue.side_effect = [data_queue, OSError("Queue allocation failed")]
    with pytest.raises(OSError, match="Queue allocation"):
        create_shared_memory_pair(1, ctx=ctx)
    data_queue.close.assert_called_once()


@pytest.mark.parametrize("endpoint", ["SharedMemorySender", "SharedMemoryReceiver"])
def test_factory_endpoint_failure_closes_both_queues(monkeypatch, endpoint):
    from py_sharedmemory import factory

    ctx = Mock()
    queues = [Mock(), Mock()]
    ctx.Queue.side_effect = queues
    monkeypatch.setattr(factory, endpoint, Mock(side_effect=MemoryError("Endpoint allocation")))
    with pytest.raises(MemoryError, match="Endpoint allocation"):
        factory.create_shared_memory_pair(1, ctx=ctx)
    for queue in queues:
        queue.close.assert_called_once()


@pytest.mark.parametrize("share", [False, True])
def test_factory_starts_a_shared_resource_tracker_only_when_needed(monkeypatch, share):
    from py_sharedmemory import factory

    ensure_running = Mock()
    monkeypatch.setattr(factory, "_SHARE_RESOURCE_TRACKER", share)
    monkeypatch.setattr(factory.resource_tracker, "ensure_running", ensure_running)
    factory.create_shared_memory_pair(1, ctx=Mock())
    assert ensure_running.called is share


@pytest.mark.parametrize("capacity", [0, None])
def test_unbounded_capacity(make_pair, capacity):
    sender, receiver = make_pair(capacity)
    for index in range(100):
        sender.put_nowait(index)
    assert [receiver.get(timeout=3) for _ in range(100)] == list(range(100))
    assert sender.wait_for_all_ack(timeout=3)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"block": False},
        {"block": False, "timeout": 10},
        {"block": False, "timeout": -10},
        {"timeout": 0},
        {"timeout": -10},
    ],
)
def test_full_nonblocking_and_immediate_timeouts(make_pair, kwargs):
    sender, receiver = make_pair()
    sender.put(1)
    with pytest.raises(Full):
        sender.put(2, **kwargs)
    assert receiver.get(timeout=3) == 1
    assert sender.wait_for_all_ack(timeout=3)
    sender.put(3, **kwargs)
    assert receiver.get(timeout=3) == 3


@pytest.mark.parametrize("capacity", [0, None, 5])
def test_outstanding_messages_never_exceed_the_metadata_queue_limit(
    make_pair, monkeypatch, capacity
):
    # macOS limits a multiprocessing queue to 32,767 unread items; simulate a tiny limit.
    monkeypatch.setattr(sender_module, "_MAX_OUTSTANDING", 2)
    sender, receiver = make_pair(capacity)
    sender.put(1)
    sender.put(2)
    with pytest.raises(Full):
        sender.put_nowait(3)
    with pytest.raises(Full):
        sender.put(3, timeout=0.05)
    assert receiver.get(timeout=3) == 1
    sender.put(3, timeout=3)
    assert [receiver.get(timeout=3) for _ in range(2)] == [2, 3]
    assert sender.wait_for_all_ack(timeout=3)


@pytest.mark.parametrize("unmap", [False, True])
def test_sender_unmaps_blocks_only_where_names_keep_them_alive(make_pair, monkeypatch, unmap):
    monkeypatch.setattr(sender_module, "_UNMAP_AFTER_WRITE", unmap)
    sender, receiver = make_pair()
    sender.put(42)
    allocation = next(iter(sender._blocks.values()))
    assert (allocation.buf is None) is unmap
    if not (unmap and sys.platform == "win32"):
        # Windows frees an unmapped block at once, which is why it never unmaps.
        assert receiver.get(timeout=3) == 42
        assert sender.wait_for_all_ack(timeout=3)


def test_put_timeout_waits_for_capacity(make_pair):
    sender, _ = make_pair()
    sender.put(1)
    start = time.monotonic()
    with pytest.raises(Full):
        sender.put(2, timeout=0.05)
    assert 0.035 <= time.monotonic() - start < 2


@pytest.mark.parametrize("kwargs", [{"block": False}, {"timeout": 0.02}, {"timeout": -1}])
def test_empty_queue(make_pair, kwargs):
    _, receiver = make_pair()
    with pytest.raises(Empty):
        receiver.get(**kwargs)


def test_wait_before_first_put_and_timeout(make_pair):
    sender, receiver = make_pair()
    assert sender.wait_for_all_ack(timeout=0)
    sender.put(1)
    assert not sender.wait_for_all_ack(timeout=0.02)
    assert receiver.get(timeout=3) == 1
    assert sender.wait_for_all_ack(timeout=3)


def test_immediate_ack_cannot_arrive_before_handle_registration():
    acknowledged = []

    class ImmediateAckQueue(LocalQueue):
        def put_nowait(self, info):
            acknowledged.append(info.name)
            sender._release_message(info.name)

    sender = SharedMemorySender(1, ImmediateAckQueue(), LocalQueue())
    try:
        for index in range(25):
            sender.put(index, timeout=0.1)
            assert sender.wait_for_all_ack(timeout=0.1)
            assert not sender._blocks
        for name in acknowledged:
            assert_unlinked(name)
    finally:
        sender.close()


def test_unknown_and_duplicate_acks_do_not_release_extra_capacity(make_pair):
    sender, receiver = make_pair()
    sender.put(1)
    name = next(iter(sender._blocks))
    assert receiver.get(timeout=3) == 1
    assert sender.wait_for_all_ack(timeout=3)
    sender._ack_queue.put_nowait(name)
    sender._ack_queue.put_nowait("unknown-shared-memory")
    sender.put(2)
    with pytest.raises(Full):
        sender.put_nowait(3)
    assert receiver.get(timeout=3) == 2
    assert sender.wait_for_all_ack(timeout=3)


@pytest.mark.parametrize("failure", ["pickle", "allocation", "register", "publish", "interrupt"])
def test_failed_put_frees_memory_and_sender_remains_usable(make_pair, monkeypatch, failure):
    sender, receiver = make_pair()
    names = []
    real_convert = sender_module.serialize

    def track(data):
        handle, info = real_convert(data)
        names.append(handle.name)
        return handle, info

    monkeypatch.setattr(sender_module, "serialize", track)
    with monkeypatch.context() as patch:
        if failure == "pickle":

            def data():
                pass

            error = (pickle.PicklingError, AttributeError)
        elif failure == "register":

            class FailingRegistry(dict):
                def __setitem__(self, name, allocation):
                    raise MemoryError("Cannot register allocation")

            patch.setattr(sender, "_blocks", FailingRegistry())
            data = 42
            error = MemoryError
        elif failure == "publish":
            patch.setattr(sender._data_queue, "put_nowait", Mock(side_effect=OSError("Publish")))
            data = 42
            error = OSError
        else:
            error = MemoryError if failure == "allocation" else KeyboardInterrupt
            patch.setattr(sender_module, "serialize", Mock(side_effect=error))
            data = 42
        with pytest.raises(error):
            sender.put(data, timeout=0.2)
    for name in names:
        assert_unlinked(name)
    assert not sender.closed
    assert sender.wait_for_all_ack(timeout=0)
    sender.put("recovered", timeout=0.2)
    assert receiver.get(timeout=3) == "recovered"
    assert sender.wait_for_all_ack(timeout=3)


def test_failed_decode_is_acknowledged_and_next_message_succeeds(make_pair):
    sender, receiver = make_pair()
    sender.put(42)
    name = next(iter(sender._blocks))
    corrupt = SharedMemory(name=name)
    corrupt.buf[0] = ord("!")
    corrupt.close()
    with pytest.raises(pickle.UnpicklingError):
        receiver.get(timeout=3)
    assert sender.wait_for_all_ack(timeout=3)
    assert_unlinked(name)
    sender.put(43, timeout=0.2)
    assert receiver.get(timeout=3) == 43


def test_invalid_message_raises_a_real_exception(make_pair):
    _, receiver = make_pair()
    receiver._data_queue.put_nowait(None)
    with pytest.raises(ValueError, match="metadata"):
        receiver.get(timeout=3)


def test_array_snapshot_is_independent_and_survives_close(make_pair):
    sender, receiver = make_pair()
    source = np.arange(262_144, dtype=np.float32)
    expected = source.copy()
    sender.put(source)
    name = next(iter(sender._blocks))
    source[:] = -1
    result = receiver.get(timeout=3)
    assert sender.wait_for_all_ack(timeout=3)
    assert_unlinked(name)
    sender.close()
    np.testing.assert_array_equal(result, expected)
    result[:] = 7
    assert np.all(source == -1)


def test_close_is_idempotent_and_releases_unread_blocks(make_pair):
    sender, receiver = make_pair(0)
    for index in range(10):
        sender.put(index)
    names = list(sender._blocks)
    sender.close()
    sender.close()
    assert sender._finalizer is not None
    assert not sender._finalizer.still_active()
    assert not sender._ack_thread.is_alive()
    for name in names:
        assert_unlinked(name)
    receiver.close()
    receiver.close()
    with pytest.raises(BrokenPipeError):
        sender.put(1)
    with pytest.raises(BrokenPipeError):
        sender.wait_for_all_ack()
    with pytest.raises(BrokenPipeError):
        receiver.get_nowait()
    with pytest.raises(BrokenPipeError):
        sender.__enter__()
    with pytest.raises(BrokenPipeError):
        receiver.__enter__()


def test_context_managers_close_unused_endpoints(make_pair):
    sender, receiver = make_pair()
    with sender, receiver:
        assert sender.wait_for_all_ack(timeout=0)
    assert sender.closed
    assert receiver.closed


def send_then_leave(sender, count, *, fail=False):
    """Leave a `with sender:` block without draining; return the abandoned block names."""
    with sender:
        for index in range(count):
            sender.put(index)
        if fail:
            raise ValueError("producer failed")
        return list(sender._blocks)


@pytest.mark.parametrize(
    ("count", "text"),
    [(1, "1 unacknowledged message that"), (2, "2 unacknowledged messages that")],
)
def test_clean_with_exit_warns_about_discarded_messages(make_pair, count, text):
    sender, _ = make_pair(0)
    with pytest.warns(RuntimeWarning, match=f"discards {text}") as caught:
        names = send_then_leave(sender, count)
    assert caught[0].filename == __file__  # Points at the user's `with` statement
    assert sender.closed
    for name in names:
        assert_unlinked(name)


@pytest.mark.parametrize("ending", ["drained", "exception", "close"])
def test_drained_failed_and_explicit_closes_do_not_warn(make_pair, ending):
    sender, receiver = make_pair()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        if ending == "drained":
            with sender:
                sender.put(1)
                assert receiver.get(timeout=3) == 1
                assert sender.wait_for_all_ack(timeout=3)
        elif ending == "exception":
            with pytest.raises(ValueError, match="producer failed"):
                send_then_leave(sender, 1, fail=True)
        else:
            sender.put(1)
            sender.close()
    assert sender.closed


def test_process_exit_cleanup_warns_about_discarded_messages(make_pair):
    sender, _ = make_pair()
    sender.put(1)
    with pytest.warns(RuntimeWarning, match="discards 1 unacknowledged message"):
        sender._finalizer()
    assert sender.closed


def test_warning_as_error_still_closes_the_sender(make_pair):
    sender, _ = make_pair()
    sender.put(1)
    name = next(iter(sender._blocks))
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(RuntimeWarning, match="unacknowledged"), sender:
            pass
    assert sender.closed
    assert_unlinked(name)


def test_ack_channel_failure_closes_without_joining_its_own_thread():
    class BrokenAckQueue(LocalQueue):
        def get(self, **kwargs):
            raise EOFError("Acknowledgement pipe failed")

    sender = SharedMemorySender(1, LocalQueue(), BrokenAckQueue())
    try:
        with suppress(BrokenPipeError):
            sender.put(1)
        sender._ack_thread.join(3)
        assert not sender._ack_thread.is_alive()
        assert sender.closed
        assert not sender._blocks
        with pytest.raises(BrokenPipeError) as caught:
            sender.wait_for_all_ack(timeout=0.1)
        assert isinstance(caught.value.__cause__, EOFError)
    finally:
        sender.close()


def test_unused_sender_can_be_pickled_for_a_child_process():
    sender = SharedMemorySender(3, "data-queue", "ack-queue")
    clone = pickle.loads(pickle.dumps(sender))
    assert clone.capacity == 3
    assert clone._data_queue == "data-queue"
    assert clone._ack_queue == "ack-queue"
    assert clone._process_id is None
    assert clone._condition is not sender._condition
    assert isinstance(clone._condition, threading.Condition)


def test_initialized_sender_rejects_use_from_another_process(make_pair):
    sender, receiver = make_pair()
    sender.put(1)
    owner = sender._process_id
    sender._process_id = os.getpid() + 1
    try:
        for call in (lambda: sender.put(2), sender.wait_for_all_ack, sender.close):
            with pytest.raises(RuntimeError, match="another process"):
                call()
        with pytest.raises(RuntimeError, match="another process"), sender:
            pass
    finally:
        sender._process_id = owner
    assert not sender.closed
    assert receiver.get(timeout=3) == 1
    assert sender.wait_for_all_ack(timeout=3)


def test_initialized_sender_cannot_be_pickled(make_pair):
    sender, receiver = make_pair()
    sender.put(1)
    with pytest.raises(RuntimeError, match="before the first put"):
        sender.__getstate__()
    assert receiver.get(timeout=3) == 1


@pytest.mark.parametrize("failure", ["thread", "start", "finalizer"])
def test_ack_initialization_failure_can_be_retried(make_pair, monkeypatch, failure):
    sender, receiver = make_pair()
    with monkeypatch.context() as patch:
        error = Mock(side_effect=RuntimeError("Initialization failed"))
        if failure == "thread":
            patch.setattr(threading, "Thread", error)
        elif failure == "start":
            patch.setattr(threading.Thread, "start", error)
        else:
            patch.setattr(sender_module, "Finalize", error)
        with pytest.raises(RuntimeError, match="Initialization failed"):
            sender.put(1)
    assert sender._process_id is None
    assert sender._producer_owner.value == 0
    assert sender._ack_thread is None
    assert sender._finalizer is None
    sender.put(2)
    assert receiver.get(timeout=3) == 2
    assert sender.wait_for_all_ack(timeout=3)


def test_ack_initialization_failure_without_owner_can_be_retried(monkeypatch):
    sender = SharedMemorySender(1, LocalQueue(), LocalQueue())
    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", Mock(side_effect=RuntimeError("No thread")))
            with pytest.raises(RuntimeError, match="No thread"):
                sender.put(1)
        assert sender._process_id is None
        assert sender._finalizer is None
        assert sender.wait_for_all_ack(timeout=0)
        sender.put(2)
        assert sender._ack_thread.is_alive()
    finally:
        sender.close()


def test_receiver_closes_ack_feeder_even_if_data_queue_close_fails(monkeypatch):
    allocation, message = serialize(42)
    data_queue = LocalQueue()
    ack_queue = mp.get_context("spawn").Queue()
    receiver = SharedMemoryReceiver(data_queue, ack_queue)
    try:
        data_queue.put_nowait(message)
        assert receiver.get() == 42
        assert ack_queue.get(timeout=3) == message.name
        monkeypatch.setattr(
            data_queue, "close", Mock(side_effect=OSError("Data queue close failed"))
        )
        with pytest.raises(OSError, match="Data queue close failed"):
            receiver.close()
        assert receiver.closed
        assert ack_queue._closed
        assert not ack_queue._thread.is_alive()
        receiver.close()
    finally:
        release_shared_memory(allocation)
        ack_queue.close()
        ack_queue.join_thread()


def test_cleanup_error_does_not_leave_other_allocations_or_queues_open(make_pair, monkeypatch):
    sender, receiver = make_pair(0)
    for index in range(3):
        sender.put(index)
    names = list(sender._blocks)
    real_close = sender_module.release_shared_memory

    def fail_one_close(handle):
        real_close(handle)
        if handle.name == names[0]:
            raise OSError("Cleanup failure")

    monkeypatch.setattr(sender_module, "release_shared_memory", fail_one_close)
    with pytest.raises(ExceptionGroup, match="Failed to close"):
        sender.close()
    assert sender.closed
    assert not sender._ack_thread.is_alive()
    assert sender._data_queue._closed
    assert receiver._ack_queue._closed
    for name in names:
        assert_unlinked(name)


def test_drain_waits_until_shared_memory_cleanup_finishes(make_pair, monkeypatch):
    sender, receiver = make_pair()
    sender.put(1)
    name = next(iter(sender._blocks))
    cleanup_started = threading.Event()
    allow_cleanup = threading.Event()
    wait_started = threading.Event()
    wait_finished = threading.Event()
    real_close = sender_module.release_shared_memory

    def slow_close(handle):
        cleanup_started.set()
        if not allow_cleanup.wait(3):
            raise TimeoutError("Test did not release cleanup")
        real_close(handle)

    monkeypatch.setattr(sender_module, "release_shared_memory", slow_close)

    def wait():
        wait_started.set()
        result = sender.wait_for_all_ack(3)
        wait_finished.set()
        return result

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert receiver.get(timeout=3) == 1
        assert cleanup_started.wait(3)
        future = pool.submit(wait)
        assert wait_started.wait(3)
        try:
            assert not wait_finished.wait(0.05)
        finally:
            allow_cleanup.set()
        assert future.result(timeout=3)
    assert_unlinked(name)


def test_ack_and_cleanup_errors_are_both_reported():
    class BrokenAckQueue(LocalQueue):
        def get(self, **kwargs):
            raise EOFError("Acknowledgement pipe failed")

        def close(self):
            raise OSError("Queue close failed")

    sender = SharedMemorySender(1, LocalQueue(), BrokenAckQueue())
    try:
        with suppress(BrokenPipeError):
            sender.put(1)
        sender._ack_thread.join(3)
        assert not sender._ack_thread.is_alive()
        with pytest.raises(BrokenPipeError) as caught:
            sender.wait_for_all_ack(timeout=0.1)
        errors = caught.value.__cause__
        assert isinstance(errors, ExceptionGroup)
        assert isinstance(errors.exceptions[0], EOFError)
        assert isinstance(errors.exceptions[1], ExceptionGroup)
        assert isinstance(errors.exceptions[1].exceptions[0], OSError)
    finally:
        sender.close()
