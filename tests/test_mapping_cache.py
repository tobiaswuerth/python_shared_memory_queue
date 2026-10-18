"""Receivers keep their mappings of blocks the sender reuses, instead of remapping each time."""

import errno
import gc
import multiprocessing as mp
import os
import sys
import threading
import weakref

import numpy as np
import pytest

from py_sharedmemory import SharedMemoryReceiver, SharedMemorySender, convert
from py_sharedmemory import receiver as receiver_module
from py_sharedmemory import sender as sender_module
from py_sharedmemory.convert import (
    SharedMemoryMessage,
    create_shared_memory,
    release_shared_memory,
    serialize,
)

from .helpers import LocalQueue, assert_unlinked


@pytest.fixture
def attachments(monkeypatch):
    """Names the receiver attached to, in order."""
    names = []
    real = convert.SharedMemory

    def record(*args, **kwargs):
        block = real(*args, **kwargs)
        if not kwargs.get("create"):
            names.append(block.name)
        return block

    monkeypatch.setattr(convert, "SharedMemory", record)
    return names


@pytest.fixture
def standalone_receiver():
    """A receiver on a real metadata queue, fed by the test instead of a sender."""
    data_queue = mp.get_context("spawn").Queue()
    acks = LocalQueue()
    receiver = SharedMemoryReceiver(data_queue, acks)
    yield receiver, data_queue, acks
    receiver.close()
    type(data_queue).close(data_queue)
    data_queue.join_thread()


def write(block, data, sequence, token=None):
    """Serialize `data` into `block` as a sender would, and return its metadata."""
    _, message = serialize(data, sequence, lambda size: block)
    return message._replace(block=block._block_token if token is None else token)


def cached_blocks(receiver):
    cache = getattr(receiver._mappings, "cache", None)
    return [] if cache is None else list(cache.blocks.values())


def test_messages_name_the_block_they_reuse():
    data_queue = LocalQueue()
    sender = SharedMemorySender(1, data_queue, LocalQueue())
    try:
        sender.put("first")
        first = data_queue.get_nowait()
        sender._acknowledge(first.sequence)
        sender.put("second")
        second = data_queue.get_nowait()
        assert second.name == first.name
        assert second.block == first.block != 0
    finally:
        sender.close()


@pytest.mark.parametrize("reason", ["oversized", "unmapped"])
def test_blocks_that_will_not_be_reused_carry_no_identity(monkeypatch, reason):
    if reason == "oversized":
        monkeypatch.setattr(sender_module, "_MAX_POOLED_BYTES", 1)
    else:
        monkeypatch.setattr(sender_module, "_UNMAP_EXCESS_BLOCKS", True)
        monkeypatch.setattr(sender_module, "_MAX_MAPPED_BLOCKS", 0)
    data_queue = LocalQueue()
    sender = SharedMemorySender(1, data_queue, LocalQueue())
    try:
        sender.put("used once")
        assert data_queue.get_nowait().block == 0
    finally:
        sender.close()


def test_receiver_maps_a_recycled_block_once(make_pair, attachments):
    sender, receiver = make_pair()
    for index in range(5):
        sender.put(np.full(50_000, index), timeout=3)
        np.testing.assert_array_equal(receiver.get(timeout=3), np.full(50_000, index))
        assert sender.wait_for_all_ack(timeout=3)
    assert len(attachments) == 1


def test_one_off_blocks_are_released_after_reading(standalone_receiver, attachments):
    receiver, data_queue, acks = standalone_receiver
    block = create_shared_memory(4_096)
    try:
        data_queue.put(write(block, "once", 1, token=0))
        assert receiver.get(timeout=3) == "once"
        assert cached_blocks(receiver) == []
        assert acks.get_nowait() == 1
    finally:
        release_shared_memory(block)


def test_a_new_block_behind_a_reused_name_is_attached_again(standalone_receiver, attachments):
    # POSIX names repeat once freed; the identity keeps a stale mapping from being used.
    receiver, data_queue, _ = standalone_receiver
    block = create_shared_memory(4_096)
    try:
        data_queue.put(write(block, "old", 1, token=111))
        assert receiver.get(timeout=3) == "old"
        data_queue.put(write(block, "new", 2, token=222))
        assert receiver.get(timeout=3) == "new"
        assert attachments == [block.name, block.name]
    finally:
        release_shared_memory(block)


def test_cache_keeps_the_most_recent_mappings(standalone_receiver, monkeypatch):
    monkeypatch.setattr(receiver_module, "_MAX_CACHED_MAPPINGS", 2)
    receiver, data_queue, _ = standalone_receiver
    blocks = [create_shared_memory(4_096) for _ in range(3)]
    try:
        for index, block in enumerate(blocks):
            data_queue.put(write(block, index, index + 1))
            assert receiver.get(timeout=3) == index
        cached = cached_blocks(receiver)
        assert [block.name for block in cached] == [blocks[1].name, blocks[2].name]
    finally:
        for block in blocks:
            release_shared_memory(block)


def test_cache_stays_within_its_byte_limit(standalone_receiver, monkeypatch):
    monkeypatch.setattr(receiver_module, "_MAX_CACHED_BYTES", 3 * 2**20)
    receiver, data_queue, _ = standalone_receiver
    blocks = [create_shared_memory(2**20) for _ in range(4)]
    try:
        for index, block in enumerate(blocks):
            data_queue.put(write(block, index, index + 1))
            assert receiver.get(timeout=3) == index
        assert [block.name for block in cached_blocks(receiver)] == [
            block.name for block in blocks[1:]
        ]
    finally:
        for block in blocks:
            release_shared_memory(block)


def test_cached_block_freed_by_its_sender_still_delivers_its_message(standalone_receiver):
    # The producer quits after writing a message into a block this consumer already maps:
    # the mapping keeps the message readable instead of losing it.
    receiver, data_queue, acks = standalone_receiver
    block = create_shared_memory(4_096)
    data_queue.put(write(block, "first", 1))
    assert receiver.get(timeout=3) == "first"
    data_queue.put(write(block, "last words", 2))
    name = block.name
    release_shared_memory(block)  # The sender closes
    assert receiver.get(timeout=3) == "last words"
    assert [acks.get_nowait() for _ in range(2)] == [1, 2]
    receiver.close()
    assert_unlinked(name)


def test_each_thread_keeps_its_own_mappings(standalone_receiver):
    receiver, data_queue, _ = standalone_receiver
    block = create_shared_memory(4_096)
    seen = {}

    def receive(label):
        seen[label] = (receiver.get(timeout=3), cached_blocks(receiver))

    try:
        data_queue.put(write(block, "main", 1))
        receive("main")
        data_queue.put(write(block, "worker", 2))
        worker = threading.Thread(target=receive, args=("worker",))
        worker.start()
        worker.join(5)
        assert seen["main"][0] == "main"
        assert seen["worker"][0] == "worker"
        (main_mapping,) = seen["main"][1]
        (worker_mapping,) = seen["worker"][1]
        assert main_mapping is not worker_mapping
    finally:
        release_shared_memory(block)


def test_a_finished_threads_mappings_are_released(standalone_receiver):
    receiver, data_queue, _ = standalone_receiver
    block = create_shared_memory(4_096)
    mapping = []

    def receive():
        receiver.get(timeout=3)
        mapping.append(weakref.ref(cached_blocks(receiver)[0]))

    try:
        data_queue.put(write(block, "work", 1))
        worker = threading.Thread(target=receive)
        worker.start()
        worker.join(5)
        del worker
        gc.collect()
        assert mapping[0]() is None
        assert len(receiver._caches) == 0
    finally:
        release_shared_memory(block)


def test_close_releases_every_threads_mappings(standalone_receiver):
    receiver, data_queue, _ = standalone_receiver
    block = create_shared_memory(4_096)
    mappings = []
    received, release = threading.Event(), threading.Event()

    def receive_and_stay():
        receiver.get(timeout=3)
        mappings.extend(cached_blocks(receiver))
        received.set()
        release.wait(5)  # Still alive, with its mapping, while close() runs

    worker = threading.Thread(target=receive_and_stay)
    try:
        data_queue.put(write(block, "main", 1))
        receiver.get(timeout=3)
        mappings.extend(cached_blocks(receiver))
        data_queue.put(write(block, "thread", 2))
        worker.start()
        assert received.wait(5)
        assert len(mappings) == 2
        receiver.close()
        assert all(mapping.buf is None for mapping in mappings)
    finally:
        release.set()
        worker.join(5)
        release_shared_memory(block)


def test_close_leaves_a_mapping_still_in_use_to_its_thread(standalone_receiver):
    receiver, data_queue, _ = standalone_receiver
    block = create_shared_memory(4_096)
    try:
        data_queue.put(write(block, "busy", 1))
        assert receiver.get(timeout=3) == "busy"
        (mapping,) = cached_blocks(receiver)
        view = mapping.buf[:4]  # Another thread is still copying out of it
        receiver.close()  # Must not raise; the reader keeps the memory until it is done
        assert bytes(view) == bytes(block.buf[:4])
        view.release()
        mapping.close()
    finally:
        release_shared_memory(block)


def test_a_block_over_the_byte_limit_is_still_readable(standalone_receiver, monkeypatch):
    monkeypatch.setattr(receiver_module, "_MAX_CACHED_BYTES", 1)
    receiver, data_queue, _ = standalone_receiver
    block = create_shared_memory(4_096)
    try:
        data_queue.put(write(block, "large", 1))
        assert receiver.get(timeout=3) == "large"
        assert len(cached_blocks(receiver)) == 1  # The newest mapping always stays
    finally:
        release_shared_memory(block)


def open_descriptors():
    return len(os.listdir("/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file descriptors")
def test_a_cached_mapping_holds_one_descriptor(standalone_receiver):
    receiver, data_queue, _ = standalone_receiver
    warm_up, counted = create_shared_memory(4_096), create_shared_memory(4_096)
    try:
        data_queue.put(write(warm_up, "warm-up", 1))  # Sets up the queue's own resources
        receiver.get(timeout=3)
        before = open_descriptors()
        data_queue.put(write(counted, "counted", 2))
        assert receiver.get(timeout=3) == "counted"
        assert open_descriptors() - before == 1
    finally:
        release_shared_memory(warm_up)
        release_shared_memory(counted)


def test_receiver_keeps_no_more_mappings_than_the_pair_has_blocks(standalone_receiver):
    # A sender never has more blocks alive than its capacity, so more mappings are dead weight.
    _, data_queue, acks = standalone_receiver
    receiver = SharedMemoryReceiver(data_queue, acks, capacity=2)
    blocks = [create_shared_memory(4_096) for _ in range(3)]
    try:
        for index, block in enumerate(blocks):
            data_queue.put(write(block, index, index + 1))
            assert receiver.get(timeout=3) == index
        assert [block.name for block in cached_blocks(receiver)] == [
            blocks[1].name,
            blocks[2].name,
        ]
    finally:
        receiver.close()
        for block in blocks:
            release_shared_memory(block)


def test_out_of_descriptors_releases_kept_mappings_and_retries(standalone_receiver, monkeypatch):
    receiver, data_queue, _ = standalone_receiver
    first, second = create_shared_memory(4_096), create_shared_memory(4_096)
    real_attach = receiver_module.attach
    failures = []

    def attach_once_out_of_descriptors(message):
        if not failures:
            failures.append(message.name)
            raise OSError(errno.EMFILE, "Too many open files")
        return real_attach(message)

    try:
        data_queue.put(write(first, "kept", 1))
        assert receiver.get(timeout=3) == "kept"
        (kept,) = cached_blocks(receiver)
        monkeypatch.setattr(receiver_module, "attach", attach_once_out_of_descriptors)
        data_queue.put(write(second, "after retry", 2))
        assert receiver.get(timeout=3) == "after retry"
        assert failures == [second.name]
        assert kept.buf is None  # Released to free its descriptor
        assert [block.name for block in cached_blocks(receiver)] == [second.name]
    finally:
        release_shared_memory(first)
        release_shared_memory(second)


def test_out_of_descriptors_without_kept_mappings_is_raised(standalone_receiver, monkeypatch):
    receiver, data_queue, acks = standalone_receiver
    block = create_shared_memory(4_096)

    def out_of_descriptors(message):
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(receiver_module, "attach", out_of_descriptors)
    try:
        data_queue.put(write(block, "unreadable", 1))
        with pytest.raises(OSError, match="Too many open files"):
            receiver.get(timeout=3)
        assert acks.empty()  # Never attached, so nothing to acknowledge
    finally:
        release_shared_memory(block)


def test_receiver_state_for_pickling_leaves_out_its_mappings(standalone_receiver):
    receiver, data_queue, _ = standalone_receiver
    block = create_shared_memory(4_096)
    try:
        data_queue.put(write(block, "cached", 1))
        receiver.get(timeout=3)
        state = receiver.__getstate__()
        assert "_mappings" not in state
        assert "_caches" not in state
        clone = SharedMemoryReceiver.__new__(SharedMemoryReceiver)
        clone.__setstate__(state)
        assert cached_blocks(clone) == []
        assert len(clone._caches) == 0
    finally:
        release_shared_memory(block)


@pytest.mark.parametrize(
    "bad_block",
    [-1, True, 1.0, None],
)
def test_invalid_block_identity_is_rejected(bad_block):
    with pytest.raises(ValueError, match="metadata"):
        convert.validate_message(SharedMemoryMessage("name", (1,), 0, bad_block))
