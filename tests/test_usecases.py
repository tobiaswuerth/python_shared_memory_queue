"""End-to-end scenarios modelled on how the library is deployed: real processes, real payloads.

Each test reads as a small program a user might write. Child workloads live in
`tests/workloads.py`, so spawned children import little. Every wait in the parent goes
through `receive` or `send`, which fail at once when a child dies instead of hanging.
"""

import gc
import multiprocessing as mp
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import suppress
from multiprocessing.shared_memory import SharedMemory
from queue import Empty, Full

import numpy as np
import pytest

from py_sharedmemory import create_shared_memory_pair
from py_sharedmemory.convert import release_shared_memory

from . import workloads
from .helpers import assert_unlinked, block_names, pooled_names

SPAWN = mp.get_context("spawn")


def stop(processes):
    for process in processes:
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join(5)


def close_queues(*queues):
    for queue in queues:
        queue.close()
        queue.join_thread()


def _fail_if_dead(watch):
    for process in watch:
        if process.exitcode not in (None, 0):
            pytest.fail(f"{process.name} exited with code {process.exitcode}")


def receive(source, watch, timeout=20):
    """Get from a receiver or queue, failing at once instead of waiting on a dead peer."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return source.get(timeout=0.2)
        except Empty:
            _fail_if_dead(watch)
            if time.monotonic() > deadline:
                raise


def send(sender, item, watch, timeout=20):
    """Put to a sender, failing at once instead of waiting for a dead consumer."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return sender.put(item, timeout=0.2)
        except Full:
            _fail_if_dead(watch)
            if time.monotonic() > deadline:
                raise


def test_orchestrator_launches_pipelines_without_keeping_their_endpoints(ctx):
    # An orchestrator wires producer -> consumer pipelines in a loop, keeps only the
    # processes, and starts the whole system at once. Like a multiprocessing.Queue, a pair
    # must outlive its children's start-up, so it waits for both to start, then lets the
    # endpoints go.
    ready, outcomes, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    processes = []
    try:
        for tag in range(4):
            sender, receiver = create_shared_memory_pair(2, ctx=ctx)
            producer_args = (sender, tag, 5, ready, outcomes, start)
            pipeline = [
                ctx.Process(target=workloads.tagged_producer, args=producer_args),
                ctx.Process(
                    target=workloads.tagged_consumer, args=(receiver, tag, ready, outcomes)
                ),
            ]
            for process in pipeline:
                process.start()
            processes += pipeline
            assert [receive(ready, processes, 30) for _ in pipeline] == [("ready", tag)] * 2
            del sender, receiver, pipeline, producer_args
            gc.collect()
        start.set()
        reports = [receive(outcomes, processes, 60) for _ in processes]
    finally:
        start.set()
        stop(processes)
        close_queues(ready, outcomes)
    producers = {tag: result for role, tag, result in reports if role == "producer"}
    consumers = {tag: result for role, tag, result in reports if role == "consumer"}
    assert producers == dict.fromkeys(range(4), "ok")
    assert consumers == {tag: [(index, True) for index in range(5)] for tag in range(4)}


def test_one_pair_per_producer_feeds_one_parent():
    # The README's advice for several producers: one pair each. Rebinding the loop variables
    # releases each previous sender, exactly as ordinary code does.
    ready, outcomes = SPAWN.Queue(), SPAWN.Queue()
    receivers, producers = {}, []
    try:
        for tag in range(6):
            sender, receiver = create_shared_memory_pair(2, ctx=SPAWN)
            producer = SPAWN.Process(
                target=workloads.tagged_producer, args=(sender, tag, 8, ready, outcomes)
            )
            producer.start()
            receivers[tag] = receiver
            producers.append(producer)
        received = {tag: [] for tag in receivers}
        live, deadline = dict(receivers), time.monotonic() + 60
        while live and time.monotonic() < deadline:
            for tag, receiver in list(live.items()):
                try:
                    message = receiver.get(timeout=0.05)
                except Empty:
                    _fail_if_dead(producers)
                    continue
                if message is None:
                    del live[tag]
                    continue
                message_tag, index, payload = message
                assert message_tag == tag
                assert (payload == tag * 1_000 + index).all()
                received[tag].append(index)
        reports = [receive(outcomes, producers, 30) for _ in producers]
    finally:
        stop(producers)
        for receiver in receivers.values():
            receiver.close()
        close_queues(ready, outcomes)
    assert received == {tag: list(range(8)) for tag in range(6)}
    assert sorted(reports) == [("producer", tag, "ok") for tag in range(6)]


def test_worker_pool_processes_every_task_exactly_once():
    # Parent fans tasks of very different sizes out to workers sharing one receiver.
    sender, receiver = create_shared_memory_pair(6, ctx=SPAWN)
    results = SPAWN.Queue()
    workers = [
        SPAWN.Process(target=workloads.checksum_worker, args=(receiver, results)) for _ in range(3)
    ]
    for worker in workers:
        worker.start()
    rng = np.random.default_rng(7)
    sizes = [100, 2_000_000, 50_000, 8, 700_000, 3_000_000, 1_000, 250_000] * 4
    tasks = {
        task_id: rng.integers(0, 255, size=size, dtype=np.uint8)
        for task_id, size in enumerate(sizes)
    }
    try:
        with sender:
            for task_id, payload in tasks.items():
                send(sender, (task_id, payload), workers)
            for _ in workers:
                send(sender, None, workers)
            checksums = dict(receive(results, workers, 30) for _ in tasks)
            assert sender.wait_for_all_ack(timeout=20)
    finally:
        stop(workers)
        receiver.close()
        close_queues(results)
    assert checksums == {task_id: workloads.checksum(task) for task_id, task in tasks.items()}
    assert all(worker.exitcode == 0 for worker in workers)


def test_two_stage_pipeline_transforms_every_message():
    # Producer -> transform process -> consumer: the middle stage consumes one pair and
    # produces into another, so one process holds a receiver and a sender.
    first_sender, first_receiver = create_shared_memory_pair(2, ctx=SPAWN)
    second_sender, second_receiver = create_shared_memory_pair(2, ctx=SPAWN)
    stage = SPAWN.Process(target=workloads.doubling_stage, args=(first_receiver, second_sender))
    stage.start()
    del second_sender  # Only the stage produces into the second pair
    outputs = []
    try:
        with first_sender:
            for index in range(20):
                send(first_sender, (index, np.arange(50_000, dtype=np.int64) * index), [stage])
                if index >= 2:
                    outputs.append(receive(second_receiver, [stage]))
            send(first_sender, None, [stage])
            while (message := receive(second_receiver, [stage])) is not None:
                outputs.append(message)
            assert first_sender.wait_for_all_ack(timeout=20)
    finally:
        stop([stage])
        second_receiver.close()
        first_receiver.close()
    assert [index for index, _ in outputs] == list(range(20))
    for index, payload in outputs:
        np.testing.assert_array_equal(payload, np.arange(50_000, dtype=np.int64) * index * 2)
    assert stage.exitcode == 0


def test_environment_worker_answers_every_action():
    # RL actor loop: send an action, wait for the observation, repeat; one pair each way.
    actions_sender, actions_receiver = create_shared_memory_pair(1, ctx=SPAWN)
    observations_sender, observations_receiver = create_shared_memory_pair(1, ctx=SPAWN)
    worker = SPAWN.Process(
        target=workloads.environment_worker, args=(actions_receiver, observations_sender)
    )
    worker.start()
    del observations_sender
    try:
        with actions_sender:
            for step in range(150):
                send(actions_sender, (step, step % 7), [worker])
                observed_step, observation = receive(observations_receiver, [worker])
                assert observed_step == step
                assert observation.shape == (4, 84, 84)
                assert (observation == step % 7).all()
            send(actions_sender, None, [worker])
            assert actions_sender.wait_for_all_ack(timeout=20)
    finally:
        stop([worker])
        observations_receiver.close()
        actions_receiver.close()
    assert worker.exitcode == 0


def test_backpressure_keeps_shared_memory_within_capacity():
    # A fast producer and a slow consumer: put blocks instead of piling up blocks.
    sender, receiver = create_shared_memory_pair(2, ctx=SPAWN)
    report = SPAWN.Queue()
    producer = SPAWN.Process(target=workloads.bounded_producer, args=(sender, 30, 500_000, report))
    producer.start()
    try:
        for index in range(30):
            payload = receive(receiver, [producer])
            assert payload.shape == (500_000,)
            assert payload[0] == payload[-1] == index % 251
            time.sleep(0.01)
        most_blocks = receive(report, [producer])
    finally:
        stop([producer])
        receiver.close()
        close_queues(report)
    assert most_blocks <= 2
    assert producer.exitcode == 0


def test_mixed_real_world_messages_arrive_exactly():
    # RL batches, blobs, frame buffers, read-only Fortran arrays, strided views, metadata,
    # text and empty arrays, interleaved so blocks are reused across sizes.
    sender, receiver = create_shared_memory_pair(3, ctx=SPAWN)
    producer = SPAWN.Process(target=workloads.mixed_producer, args=(sender, 40))
    producer.start()
    try:
        for index in range(40):
            workloads.assert_same(receive(receiver, [producer]), workloads.make_message(index))
        assert receive(receiver, [producer]) is None
    finally:
        stop([producer])
        receiver.close()
    assert producer.exitcode == 0


def test_threads_of_one_producer_process_share_its_sender():
    sender, receiver = create_shared_memory_pair(4, ctx=SPAWN)
    producer = SPAWN.Process(target=workloads.threaded_producer, args=(sender, 4, 25))
    producer.start()
    received = {thread: [] for thread in range(4)}
    try:
        while (message := receive(receiver, [producer])) is not None:
            thread, index, payload = message
            assert (payload == thread * 1_000 + index).all()
            received[thread].append(index)
    finally:
        stop([producer])
        receiver.close()
    # Each thread's messages arrive complete and in that thread's order.
    assert received == {thread: list(range(25)) for thread in range(4)}
    assert producer.exitcode == 0


def test_recycled_consumer_hands_over_to_its_replacement():
    # A worker exits after a fixed number of tasks, like maxtasksperchild, and a new one
    # takes over the same receiver without losing or repeating a message.
    sender, receiver = create_shared_memory_pair(0, ctx=SPAWN)
    results = SPAWN.Queue()
    try:
        with sender:
            for index in range(20):
                sender.put((index, np.full(20_000, index)), timeout=20)
            handled = []
            for _ in range(2):
                worker = SPAWN.Process(
                    target=workloads.partial_consumer, args=(receiver, 10, results)
                )
                worker.start()
                handled += receive(results, [worker], 30)
                stop([worker])
                assert worker.exitcode == 0
            assert sender.wait_for_all_ack(timeout=20)
    finally:
        receiver.close()
        close_queues(results)
    assert [index for index, _ in handled] == list(range(20))
    assert all((payload == index).all() for index, payload in handled)


def eventually(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail("Condition not reached in time")
        time.sleep(0.01)


@pytest.mark.parametrize("ack_lost", [True, False], ids=["ack-lost", "ack-delivered"])
def test_crashed_consumer_neither_hangs_the_producer_nor_loses_undelivered_messages(ack_lost):
    sender, receiver = create_shared_memory_pair(3, ctx=SPAWN)
    results = SPAWN.Queue()
    crashed = SPAWN.Process(target=workloads.crashing_consumer, args=(receiver, results, ack_lost))
    crashed.start()
    try:
        sender.put((0, np.full(30_000, 0)), timeout=20)
        assert results.get(timeout=30) == "received"
        stop([crashed])
        assert crashed.exitcode == 17
        # A lost acknowledgement keeps its message's capacity taken; a delivered one frees it.
        if ack_lost:
            assert not sender.wait_for_all_ack(timeout=0.2)  # Nobody is left to send it
        else:
            assert sender.wait_for_all_ack(timeout=20)
        free_slots = 2 if ack_lost else 3
        for index in range(1, free_slots + 1):
            sender.put((index, np.full(30_000, index)), timeout=20)
        with pytest.raises(Full):  # No consumer: put times out instead of hanging
            sender.put("one too many", timeout=0.2)
        # A replacement drains everything the crashed consumer never received.
        replacement = SPAWN.Process(
            target=workloads.partial_consumer, args=(receiver, free_slots, results)
        )
        replacement.start()
        drained = receive(results, [replacement], 30)
        stop([replacement])
        assert [index for index, _ in drained] == list(range(1, free_slots + 1))
        eventually(lambda: len(sender._blocks) == (1 if ack_lost else 0))
        # Closing never blocks, even with an acknowledgement lost for good.
        names = block_names(sender) + pooled_names(sender)
        started = time.monotonic()
        sender.close()
        assert time.monotonic() - started < 5
        for name in names:
            assert_unlinked(name)
    finally:
        sender.close()
        receiver.close()
        close_queues(results)


def test_killing_an_idle_consumer_does_not_starve_the_others():
    # A supervisor scales the pool down by terminating a worker that is waiting for work.
    sender, receiver = create_shared_memory_pair(0, ctx=SPAWN)
    waiting, results = SPAWN.Event(), SPAWN.Queue()
    idle = SPAWN.Process(target=workloads.idle_consumer, args=(receiver, waiting))
    idle.start()
    survivor = None
    try:
        assert waiting.wait(30)  # It is inside get(), waiting for data
        idle.terminate()
        stop([idle])
        survivor = SPAWN.Process(target=workloads.partial_consumer, args=(receiver, 5, results))
        survivor.start()
        with sender:
            for index in range(5):
                sender.put(index, timeout=20)
            assert receive(results, [survivor], 15) == list(range(5))
            assert sender.wait_for_all_ack(timeout=20)
    finally:
        stop([idle] + ([survivor] if survivor else []))
        receiver.close()
        close_queues(results)


def test_many_consumers_receive_each_message_exactly_once():
    # Consumers race for every message; none may be lost, duplicated or stuck.
    sender, receiver = create_shared_memory_pair(8, ctx=SPAWN)
    results = SPAWN.Queue()
    workers = [
        SPAWN.Process(target=workloads.checksum_worker, args=(receiver, results)) for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    try:
        with sender:
            for task_id in range(1_500):
                send(sender, (task_id, np.full(64, task_id % 251, dtype=np.uint8)), workers)
            for _ in workers:
                send(sender, None, workers)
            handled = [receive(results, workers, 30)[0] for _ in range(1_500)]
            assert sender.wait_for_all_ack(timeout=20)
    finally:
        stop(workers)
        receiver.close()
        close_queues(results)
    assert sorted(handled) == list(range(1_500))


def test_crashed_producer_leaves_the_consumer_responsive():
    sender, receiver = create_shared_memory_pair(0, ctx=SPAWN)
    names = SPAWN.Queue()
    producer = SPAWN.Process(target=workloads.crashing_producer, args=(sender, 3, names))
    producer.start()
    block_names_sent = []
    try:
        block_names_sent = names.get(timeout=30)
        stop([producer])
        assert producer.exitcode == 17
        outcomes = []
        for _ in range(3):
            try:
                outcomes.append(int(receiver.get(timeout=20)[0]))
            except FileNotFoundError:
                outcomes.append("freed")
        # A POSIX block lives on by name after its creator dies; Windows frees it at once.
        assert outcomes == (["freed"] * 3 if sys.platform == "win32" else [0, 1, 2])
        with pytest.raises(Empty):
            receiver.get(timeout=0.2)
        started = time.monotonic()
        receiver.close()
        assert time.monotonic() - started < 5
    finally:
        receiver.close()
        close_queues(names)
        # On Linux and macOS the dead producer's blocks outlive it until unlinked.
        for name in block_names_sent:
            with suppress(FileNotFoundError):
                release_shared_memory(SharedMemory(name=name))


def test_process_pool_executor_workers_take_tasks_from_a_receiver():
    # The standard way to hand pool workers a queue: through the pool initializer.
    sender, receiver = create_shared_memory_pair(4, ctx=SPAWN)
    tasks = {task_id: np.arange(task_id * 20_000, dtype=np.int32) for task_id in range(12)}
    try:
        with (
            sender,
            ProcessPoolExecutor(
                max_workers=2,
                mp_context=SPAWN,
                initializer=workloads.attach_pool_receiver,
                initargs=(receiver,),
            ) as pool,
        ):
            futures = [pool.submit(workloads.pool_take_one) for _ in tasks]
            for task_id, payload in tasks.items():
                sender.put((task_id, payload), timeout=20)
            checksums = dict(future.result(timeout=30) for future in futures)
            assert sender.wait_for_all_ack(timeout=20)
    finally:
        receiver.close()
    assert checksums == {task_id: workloads.checksum(task) for task_id, task in tasks.items()}
