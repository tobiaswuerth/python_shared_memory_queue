import gc
import multiprocessing as mp
import subprocess
import sys
from pathlib import Path

import pytest

from py_sharedmemory import create_shared_memory_pair

from .helpers import (
    abandon_metadata,
    assert_unlinked,
    consume,
    exit_with_unreceived_data,
    join_process,
    make_payload,
    produce,
    try_foreign_sender,
    verify_payloads,
)


def test_producer_process_round_trip(make_pair, ctx):
    sender, receiver = make_pair(2, ctx=ctx)
    process = ctx.Process(target=produce, args=(sender, 50))
    process.start()
    try:
        for index in range(50):
            value, payload = receiver.get(timeout=10)
            assert value == index
            assert payload == bytearray([index]) * 65_536
        join_process(process)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_consumer_process_round_trip(make_pair, ctx):
    sender, receiver = make_pair(2, ctx=ctx)
    results = ctx.Queue()
    process = ctx.Process(target=consume, args=(receiver, 50, results))
    process.start()
    try:
        for index in range(50):
            sender.put((index, bytearray([index]) * 65_536), timeout=10)
        assert results.get(timeout=10) == list(range(50))
        assert sender.wait_for_all_ack(timeout=10)
        join_process(process)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        results.close()
        results.join_thread()


def test_both_endpoints_in_child_processes(make_pair, ctx):
    sender, receiver = make_pair(2, ctx=ctx)
    results = ctx.Queue()
    consumer = ctx.Process(target=consume, args=(receiver, 75, results))
    producer = ctx.Process(target=produce, args=(sender, 75))
    consumer.start()
    producer.start()
    try:
        assert results.get(timeout=15) == list(range(75))
        join_process(producer)
        join_process(consumer)
    finally:
        for process in (producer, consumer):
            if process.is_alive():
                process.terminate()
                process.join(5)
        results.close()
        results.join_thread()


def test_multiple_consumer_processes(make_pair, ctx):
    sender, receiver = make_pair(3, ctx=ctx)
    results = ctx.Queue()
    consumers = [ctx.Process(target=consume, args=(receiver, 20, results)) for _ in range(3)]
    for process in consumers:
        process.start()
    try:
        for index in range(60):
            sender.put((index, bytearray([index]) * 65_536), timeout=10)
        received = [index for _ in consumers for index in results.get(timeout=10)]
        assert sorted(received) == list(range(60))
        assert sender.wait_for_all_ack(timeout=10)
        for process in consumers:
            join_process(process)
    finally:
        for process in consumers:
            if process.is_alive():
                process.terminate()
                process.join(5)
        results.close()
        results.join_thread()


def test_large_messages_arrive_intact_across_consumer_processes(make_pair, ctx):
    sender, receiver = make_pair(4, ctx=ctx)
    results = ctx.Queue()
    consumers = [ctx.Process(target=verify_payloads, args=(receiver, results)) for _ in range(3)]
    for process in consumers:
        process.start()
    try:
        for index in range(60):
            sender.put((index, make_payload(index)), timeout=10)
        for _ in consumers:
            sender.put(None, timeout=10)
        received = dict(results.get(timeout=15) for _ in range(60))
        assert sender.wait_for_all_ack(timeout=10)
        # Every byte of every payload is checked in the consumer that received it.
        assert received == dict.fromkeys(range(60), True)
        for process in consumers:
            join_process(process)
    finally:
        for process in consumers:
            if process.is_alive():
                process.terminate()
                process.join(5)
        results.close()
        results.join_thread()


def test_normal_process_exit_cleans_unreceived_shared_memory(make_pair, ctx):
    sender, receiver = make_pair(1, ctx=ctx)
    ready_to_exit = ctx.Event()
    process = ctx.Process(target=exit_with_unreceived_data, args=(sender, ready_to_exit))
    process.start()
    try:
        info = receiver._data_queue.get(timeout=10)
        ready_to_exit.set()
        join_process(process)
        assert_unlinked(info.name)
    finally:
        ready_to_exit.set()
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_abandoned_metadata_does_not_hang_process_exit(make_pair, ctx):
    sender, _ = make_pair(0, ctx=ctx)
    result_in, result_out = ctx.Pipe(duplex=False)
    process = ctx.Process(target=abandon_metadata, args=(sender, result_out))
    process.start()
    result_out.close()
    try:
        assert result_in.poll(15)
        names = result_in.recv()
        assert len(names) == 1_000
        join_process(process)
        for name in names:
            assert_unlinked(name)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        result_in.close()


def test_producers_whose_senders_the_parent_dropped(ctx):
    # The documented pattern: create a pair, pass the sender to its process, keep only the
    # receiver. Process.start() drops its arguments, so the parent's sender is freed. Its
    # producer-owner slot must stay reserved, or the next pair reuses it and the producers
    # of the two pairs reject each other as "a second producing process".
    receivers, processes = [], []
    try:
        for _ in range(4):
            sender, receiver = create_shared_memory_pair(2, ctx=ctx)
            process = ctx.Process(target=produce, args=(sender, 3))
            process.start()
            del sender
            gc.collect()
            receivers.append(receiver)
            processes.append(process)
        for receiver in receivers:
            assert [receiver.get(timeout=10)[0] for _ in range(3)] == [0, 1, 2]
        for process in processes:
            join_process(process)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        for receiver in receivers:
            receiver.close()


def test_a_second_producing_process_is_rejected(make_pair, ctx):
    sender, receiver = make_pair(1, ctx=ctx)
    process = ctx.Process(target=produce, args=(sender, 1))
    process.start()
    try:
        assert receiver.get(timeout=10)[0] == 0
        join_process(process)
        with pytest.raises(RuntimeError, match="one producing process"):
            sender.put("second producer")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)


FORKED_PRODUCER = """
import multiprocessing as mp

from py_sharedmemory import create_shared_memory_pair
from tests.helpers import produce

ctx = mp.get_context("fork")
sender, receiver = create_shared_memory_pair(2, ctx=ctx)
process = ctx.Process(target=produce, args=(sender, 20))
process.start()
with receiver:
    for _ in range(20):
        receiver.get(timeout=10)
process.join(10)
sender.close()
raise SystemExit(process.exitcode)
"""


LOW_FILE_LIMIT = """
import resource

from py_sharedmemory import create_shared_memory_pair

# macOS defaults to 256 open files; unread messages must not hold descriptors.
resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
sender, receiver = create_shared_memory_pair(0)
for index in range(1_000):
    sender.put(index)
assert [receiver.get(timeout=10) for _ in range(1_000)] == list(range(1_000))
assert sender.wait_for_all_ack(timeout=10)
sender.close()
receiver.close()
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file-descriptor limits")
def test_unread_messages_hold_no_file_descriptors():
    result = subprocess.run(
        [sys.executable, "-c", LOW_FILE_LIMIT],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


UNREAD_AT_EXIT = """
from py_sharedmemory import create_shared_memory_pair

sender, receiver = create_shared_memory_pair(1)
sender.put(b"never received")
"""


def test_process_exit_with_unread_messages_warns():
    result = subprocess.run(
        [sys.executable, "-c", UNREAD_AT_EXIT],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "RuntimeWarning: Closing the sender discards 1 unacknowledged message" in result.stderr


@pytest.mark.skipif("fork" not in mp.get_all_start_methods(), reason="Requires fork")
def test_forked_producer_leaves_no_resource_tracker_entries():
    # A fresh interpreter has no resource tracker running before the fork.
    result = subprocess.run(
        [sys.executable, "-c", FORKED_PRODUCER],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "resource_tracker" not in result.stderr


@pytest.mark.skipif("fork" not in mp.get_all_start_methods(), reason="Requires fork")
# The ack thread is running on purpose; Python 3.15 warns about forking with threads.
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_forked_initialized_sender_cannot_free_parent_memory(make_pair):
    ctx = mp.get_context("fork")
    sender, receiver = make_pair(1, ctx=ctx)
    sender.put(42)
    result_in, result_out = ctx.Pipe(duplex=False)
    process = ctx.Process(target=try_foreign_sender, args=(sender, result_out))
    process.start()
    result_out.close()
    try:
        assert result_in.poll(10)
        assert "another process" in result_in.recv()
        join_process(process)
        assert receiver.get(timeout=3) == 42
        assert sender.wait_for_all_ack(timeout=3)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        result_in.close()
