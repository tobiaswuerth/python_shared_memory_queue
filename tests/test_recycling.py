import mmap
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Full

import numpy as np
import pytest

from py_sharedmemory import SharedMemorySender
from py_sharedmemory import sender as sender_module
from py_sharedmemory.convert import deserialize

from .helpers import LocalQueue, assert_unlinked, block_names, forget_mappings, pooled_names

MiB = 2**20


def round_trip(sender, receiver, data):
    sender.put(data, timeout=3)
    result = receiver.get(timeout=3)
    assert sender.wait_for_all_ack(timeout=3)
    return result


def test_messages_carry_unique_increasing_sequence_numbers():
    data_queue = LocalQueue()
    sender = SharedMemorySender(0, data_queue, LocalQueue())
    try:
        for index in range(3):
            sender.put(index)
        sequences = [data_queue.get_nowait().sequence for _ in range(3)]
        assert sequences == sorted(set(sequences))
        assert sorted(sender._blocks) == sequences
    finally:
        sender.close()


def test_acknowledged_block_is_reused_for_the_next_message(make_pair):
    sender, receiver = make_pair()
    first = np.arange(1_000, dtype=np.float64)
    np.testing.assert_array_equal(round_trip(sender, receiver, first), first)
    (name,) = pooled_names(sender)
    second = np.arange(900, dtype=np.float64)[::-1].copy()
    sender.put(second)
    assert block_names(sender) == [name]
    assert not sender._pool
    np.testing.assert_array_equal(receiver.get(timeout=3), second)
    assert sender.wait_for_all_ack(timeout=3)
    assert pooled_names(sender) == [name]


def test_repeated_acknowledgement_cannot_release_a_reused_block():
    data_queue = LocalQueue()
    sender = SharedMemorySender(1, data_queue, LocalQueue())
    try:
        sender.put("first")
        first = data_queue.get_nowait()
        sender._acknowledge(first.sequence)
        sender.put("second")
        second = data_queue.get_nowait()
        assert second.name == first.name
        sender._acknowledge(first.sequence)
        assert block_names(sender) == [second.name]
        with pytest.raises(Full):
            sender.put_nowait("third")
        assert deserialize(second) == "second"
    finally:
        sender.close()


@pytest.mark.parametrize(
    ("first_size", "second_size", "reused"),
    [
        pytest.param(8_000, 10, True, id="small-block-any-fitting-message"),
        pytest.param(4 * MiB, 5 * MiB // 2, True, id="large-block-at-least-half-full"),
        pytest.param(4 * MiB, MiB, False, id="large-block-mostly-empty"),
        # Larger than a 16 KiB page, as on Apple Silicon.
        pytest.param(1_000, 40_000, False, id="block-too-small"),
    ],
)
def test_pooled_block_is_reused_only_when_it_fits(make_pair, first_size, second_size, reused):
    sender, receiver = make_pair()
    round_trip(sender, receiver, np.zeros(first_size, dtype=np.uint8))
    (first_name,) = pooled_names(sender)
    payload = np.full(second_size, 7, dtype=np.uint8)
    sender.put(payload)
    (second_name,) = block_names(sender)
    assert (second_name == first_name) is reused
    # A miss drops the pooled block, so the pool never outgrows capacity.
    assert not sender._pool
    if not reused:
        forget_mappings(receiver)
        assert_unlinked(first_name)
    np.testing.assert_array_equal(receiver.get(timeout=3), payload)
    assert sender.wait_for_all_ack(timeout=3)


class Block:
    def __init__(self, size):
        self.size = size


@pytest.mark.parametrize(
    ("block_size", "message_size", "fits"),
    [
        pytest.param(8 * MiB, 4 * MiB, True, id="exactly-half-full"),
        pytest.param(8 * MiB, 4 * MiB - 1, False, id="just-under-half-full"),
        pytest.param(8 * MiB, 8 * MiB, True, id="exactly-full"),
        pytest.param(8 * MiB, 8 * MiB + 1, False, id="one-byte-too-large"),
        pytest.param(MiB, 1, True, id="small-block-tiny-message"),
        pytest.param(MiB + 1, 1, False, id="just-over-small-block"),
        pytest.param(MiB + 2, MiB // 2 + 1, True, id="just-over-small-block-half-full"),
    ],
)
def test_reuse_rule_boundaries(block_size, message_size, fits):
    assert sender_module._fits(Block(block_size), message_size) is fits


def test_blocks_are_rounded_up_to_whole_pages(make_pair):
    sender, _ = make_pair()
    sender.put(b"x")
    (allocation,) = sender._blocks.values()
    assert allocation.size >= mmap.PAGESIZE
    assert allocation.size % mmap.PAGESIZE == 0


def test_pool_keeps_only_the_newest_blocks(make_pair, monkeypatch):
    monkeypatch.setattr(sender_module, "_MAX_POOLED_BLOCKS", 2)
    sender, receiver = make_pair(0)
    for index in range(4):
        sender.put(index)
    names = block_names(sender)
    assert [receiver.get(timeout=3) for _ in range(4)] == list(range(4))
    assert sender.wait_for_all_ack(timeout=3)
    assert pooled_names(sender) == names[2:]
    forget_mappings(receiver)
    for name in names[:2]:
        assert_unlinked(name)


def test_pool_stays_within_its_byte_limit(make_pair, monkeypatch):
    monkeypatch.setattr(sender_module, "_MAX_POOLED_BYTES", 2 * mmap.PAGESIZE)
    sender, receiver = make_pair(0)
    for index in range(3):
        sender.put(index)
    names = block_names(sender)
    assert [receiver.get(timeout=3) for _ in range(3)] == list(range(3))
    assert sender.wait_for_all_ack(timeout=3)
    assert pooled_names(sender) == names[1:]
    forget_mappings(receiver)
    assert_unlinked(names[0])


def test_block_above_the_pool_byte_limit_is_freed_and_keeps_the_pool(make_pair, monkeypatch):
    monkeypatch.setattr(sender_module, "_MAX_POOLED_BYTES", MiB)
    sender, receiver = make_pair(0)
    round_trip(sender, receiver, b"small")
    (small,) = pooled_names(sender)
    sender.put(b"x")  # Takes the pooled block
    sender.put(np.zeros(2 * MiB, dtype=np.uint8))
    large = block_names(sender)[1]
    receiver.get(timeout=3)
    receiver.get(timeout=3)
    assert sender.wait_for_all_ack(timeout=3)
    assert pooled_names(sender) == [small]
    assert_unlinked(large)


@pytest.mark.parametrize("unmap", [False, True])
def test_only_the_first_unread_blocks_stay_mapped(make_pair, monkeypatch, unmap):
    # POSIX blocks outlive their mappings, so excess unread blocks hold no file descriptors.
    monkeypatch.setattr(sender_module, "_UNMAP_EXCESS_BLOCKS", unmap)
    monkeypatch.setattr(sender_module, "_MAX_MAPPED_BLOCKS", 2)
    sender, receiver = make_pair(0)
    for index in range(4):
        sender.put(index)
    names = block_names(sender)
    mapped = [allocation.buf is not None for allocation in sender._blocks.values()]
    assert mapped == [True, True, not unmap, not unmap]
    if unmap and sys.platform == "win32":
        return  # Windows frees an unmapped block at once, which is why it never unmaps.
    assert [receiver.get(timeout=3) for _ in range(4)] == list(range(4))
    assert sender.wait_for_all_ack(timeout=3)
    # Unmapped blocks cannot be reused without remapping, so they are freed.
    assert pooled_names(sender) == (names[:2] if unmap else names)
    if unmap:
        for name in names[2:]:
            assert_unlinked(name)
    assert sender._mapped_in_flight == 0


def test_acknowledged_unmapped_block_is_freed_and_releases_capacity(monkeypatch):
    monkeypatch.setattr(sender_module, "_UNMAP_EXCESS_BLOCKS", True)
    monkeypatch.setattr(sender_module, "_MAX_MAPPED_BLOCKS", 0)
    data_queue = LocalQueue()
    sender = SharedMemorySender(1, data_queue, LocalQueue())
    try:
        sender.put("unmapped")
        message = data_queue.get_nowait()
        (allocation,) = sender._blocks.values()
        assert allocation.buf is None
        sender._acknowledge(message.sequence)
        assert not sender._blocks
        assert not sender._pool
        assert sender._mapped_in_flight == 0
        assert_unlinked(message.name)
        assert sender.wait_for_all_ack(timeout=0)
    finally:
        sender.close()


def test_close_frees_pooled_blocks(make_pair):
    sender, receiver = make_pair()
    round_trip(sender, receiver, b"payload")
    names = pooled_names(sender)
    sender.close()
    assert not sender._pool
    forget_mappings(receiver)
    for name in names:
        assert_unlinked(name)


def test_close_reports_pool_cleanup_errors_and_frees_the_rest(make_pair, monkeypatch):
    sender, receiver = make_pair(0)
    for index in range(2):
        sender.put(index)
    assert [receiver.get(timeout=3) for _ in range(2)] == [0, 1]
    assert sender.wait_for_all_ack(timeout=3)
    names = pooled_names(sender)
    real_release = sender_module.release_shared_memory

    def fail_one(allocation):
        real_release(allocation)
        if allocation.name == names[0]:
            raise OSError("Cleanup failure")

    monkeypatch.setattr(sender_module, "release_shared_memory", fail_one)
    with pytest.raises(ExceptionGroup, match="Failed to close"):
        sender.close()
    assert not sender._pool
    forget_mappings(receiver)
    for name in names:
        assert_unlinked(name)


def test_recycled_blocks_never_mix_payloads_across_threads(make_pair):
    sender, receiver = make_pair(3)
    sizes = [1_000, 70_000, 300_000, 5_000]
    start = threading.Barrier(6)

    def produce(producer):
        start.wait(timeout=5)
        for index in range(40):
            value = (producer * 40 + index) % 251
            payload = bytes([value]) * sizes[index % len(sizes)]
            sender.put((producer, index, payload), timeout=10)

    def consume():
        start.wait(timeout=5)
        received = []
        for _ in range(40):
            producer, index, payload = receiver.get(timeout=10)
            value = (producer * 40 + index) % 251
            intact = len(payload) == sizes[index % len(sizes)] and payload.count(value) == len(
                payload
            )
            received.append((producer, index, intact))
        return received

    with ThreadPoolExecutor(max_workers=6) as pool:
        producers = [pool.submit(produce, producer) for producer in range(3)]
        consumers = [pool.submit(consume) for _ in range(3)]
        for future in producers:
            future.result(timeout=30)
        received = [item for future in consumers for item in future.result(timeout=30)]
    assert sorted(received) == [(p, i, True) for p in range(3) for i in range(40)]
    assert sender.wait_for_all_ack(timeout=3)
    assert sender._pool
