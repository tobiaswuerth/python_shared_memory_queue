import multiprocessing as mp

import pytest

from .helpers import (
    abandon_metadata,
    assert_unlinked,
    consume,
    exit_with_unreceived_data,
    join_process,
    produce,
    try_foreign_sender,
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


@pytest.mark.skipif("fork" not in mp.get_all_start_methods(), reason="Requires fork")
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
