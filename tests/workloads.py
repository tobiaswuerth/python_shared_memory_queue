"""Child-process workloads for the use-case tests, kept light so spawned children start fast."""

import os
import threading
import zlib

import numpy as np

MiB = 2**20


# --- Messages -------------------------------------------------------------------------------


def make_message(index):
    """A deterministic message of a kind real pipelines send; both sides can rebuild it."""
    rng = np.random.default_rng(index)
    kind = index % 8
    if kind == 0:  # RL rollout batch
        return {
            "step": index,
            "observations": rng.integers(0, 255, size=(8, 3, 32, 32), dtype=np.uint8),
            "actions": rng.integers(0, 6, size=8),
            "rewards": rng.random(8, dtype=np.float32),
            "dones": rng.random(8) < 0.1,
        }
    if kind == 1:  # Encoded image or serialized blob, large enough to bypass the pickle copy
        return bytes(rng.integers(0, 255, size=200_000, dtype=np.uint8))
    if kind == 2:  # Mutable frame buffer
        return bytearray(rng.integers(0, 255, size=1_500_000, dtype=np.uint8))
    if kind == 3:  # Scientific array in Fortran order, read-only snapshot
        array = np.asfortranarray(rng.random((300, 200)))
        array.flags.writeable = False
        return array
    if kind == 4:  # Strided view: arrives as a contiguous copy
        return rng.random((400, 400), dtype=np.float32)[::2, 1::3]
    if kind == 5:  # Metadata-only message
        return {"event": "checkpoint", "index": index, "tags": ["a", "b"], "ratio": 0.5}
    if kind == 6:  # Text and nested containers
        return [f"line {index}-{n} ✓" for n in range(500)], (index, None, b"tiny")
    return np.empty((0, 4), dtype=np.int16)  # Empty array


def assert_same(actual, expected):
    """Deep equality including NumPy dtype, shape, layout and writability."""
    if isinstance(expected, np.ndarray):
        assert isinstance(actual, np.ndarray), type(actual)
        assert actual.dtype == expected.dtype
        assert actual.shape == expected.shape
        assert actual.flags.writeable == expected.flags.writeable
        assert actual.flags.aligned
        np.testing.assert_array_equal(actual, expected)
    elif isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_same(actual[key], expected[key])
    elif isinstance(expected, (list, tuple)):
        assert type(actual) is type(expected)
        assert len(actual) == len(expected)
        for item, expected_item in zip(actual, expected, strict=True):
            assert_same(item, expected_item)
    else:
        assert type(actual) is type(expected)
        assert actual == expected


def checksum(payload):
    return zlib.crc32(np.ascontiguousarray(payload).view(np.uint8))


# --- Producers ------------------------------------------------------------------------------


def tagged_producer(sender, tag, count, ready, outcomes, start=None):
    """Send `count` payloads tagged with this pipeline; report start-up and the outcome.

    With `start`, wait for the go signal first, as producers do that load a model or wait
    for the whole system to come up before sending.
    """
    ready.put(("ready", tag))
    if start is not None and not start.wait(30):
        raise TimeoutError("No start signal")
    try:
        with sender:
            for index in range(count):
                sender.put((tag, index, np.full(10_000, tag * 1_000 + index)), timeout=20)
            sender.put(None, timeout=20)
            if not sender.wait_for_all_ack(timeout=20):
                raise TimeoutError("Messages were not acknowledged")
    except Exception as error:
        outcomes.put(("producer", tag, f"{type(error).__name__}: {error}"))
    else:
        outcomes.put(("producer", tag, "ok"))


def mixed_producer(sender, count):
    with sender:
        for index in range(count):
            sender.put(make_message(index), timeout=20)
        sender.put(None, timeout=20)
        if not sender.wait_for_all_ack(timeout=20):
            raise TimeoutError("Messages were not acknowledged")


def threaded_producer(sender, threads, count):
    """Several threads of one producer process share its sender, like a threaded loader."""

    def run(thread):
        for index in range(count):
            sender.put((thread, index, np.full(5_000, thread * 1_000 + index)), timeout=20)

    with sender:
        workers = [threading.Thread(target=run, args=(thread,)) for thread in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        sender.put(None, timeout=20)
        if not sender.wait_for_all_ack(timeout=20):
            raise TimeoutError("Messages were not acknowledged")


def bounded_producer(sender, count, size, report):
    """Send faster than the consumer reads and report the most blocks ever alive."""
    most_blocks = 0
    with sender:
        for index in range(count):
            sender.put(np.full(size, index % 251, dtype=np.uint8), timeout=20)
            with sender._condition:
                most_blocks = max(most_blocks, len(sender._blocks) + len(sender._pool))
        if not sender.wait_for_all_ack(timeout=20):
            raise TimeoutError("Messages were not acknowledged")
    report.put(most_blocks)


def huge_producer(sender, size, count, report):
    """Send frames larger than the sender reuses, then report what it kept for reuse."""
    with sender:
        for index in range(count):
            sender.put(np.full(size, index + 1, dtype=np.uint8), timeout=60)
        if not sender.wait_for_all_ack(timeout=60):
            raise TimeoutError("Messages were not acknowledged")
        report.put(len(sender._pool))


VARYING_SIZES = (64, 900, 30_000, 200_000, 1_100_000, 3_000_000, 8_000_000)


def varying_sizes(count):
    """Message sizes of a long, uneven stream: mostly small, now and then very large."""
    rng = np.random.default_rng(11)
    weights = np.array([30, 20, 15, 15, 10, 7, 3]) / 100
    return [int(size) for size in rng.choice(VARYING_SIZES, size=count, p=weights)]


def varying_producer(sender, count, report):
    """Stream messages of varying size; report the most blocks ever alive."""
    most_blocks = 0
    with sender:
        for index, size in enumerate(varying_sizes(count)):
            sender.put((index, np.full(size, index % 251, dtype=np.uint8)), timeout=20)
            with sender._condition:
                most_blocks = max(most_blocks, len(sender._blocks) + len(sender._pool))
        if not sender.wait_for_all_ack(timeout=20):
            raise TimeoutError("Messages were not acknowledged")
    report.put(most_blocks)


def crashing_producer(sender, count, names):
    """Send, let the metadata reach the pipe, report the block names, then die unclosed."""
    for index in range(count):
        sender.put(np.full(50_000, index), timeout=20)
    sender._data_queue.close()
    sender._data_queue.join_thread()  # Every message is now readable by the consumer
    names.put([block.name for block in sender._blocks.values()])
    names.close()
    names.join_thread()
    os._exit(17)


# --- Consumers ------------------------------------------------------------------------------


def tagged_consumer(receiver, tag, ready, outcomes):
    """Receive one pipeline's payloads until its sentinel; report what arrived."""
    ready.put(("ready", tag))
    received = []
    try:
        with receiver:
            while (message := receiver.get(timeout=20)) is not None:
                message_tag, index, payload = message
                intact = message_tag == tag and bool((payload == tag * 1_000 + index).all())
                received.append((index, intact))
    except Exception as error:
        received.append(f"{type(error).__name__}: {error}")
    outcomes.put(("consumer", tag, received))


def checksum_worker(receiver, results):
    """Pool worker: compute a checksum per task until a stop signal."""
    with receiver:
        while (task := receiver.get(timeout=20)) is not None:
            task_id, payload = task
            results.put((task_id, checksum(payload)))


def doubling_stage(receiver, sender):
    """Middle pipeline stage: consumer of one pair and producer of another."""
    with receiver, sender:
        while (message := receiver.get(timeout=20)) is not None:
            index, payload = message
            sender.put((index, payload * 2), timeout=20)
        sender.put(None, timeout=20)
        if not sender.wait_for_all_ack(timeout=20):
            raise TimeoutError("Messages were not acknowledged")


def environment_worker(actions, observations):
    """RL environment: answer each action with an observation until told to stop."""
    with actions, observations:
        while (action := actions.get(timeout=20)) is not None:
            step, value = action
            observations.put((step, np.full((4, 84, 84), value, dtype=np.float32)), timeout=20)
        if not observations.wait_for_all_ack(timeout=20):
            raise TimeoutError("Observations were not acknowledged")


class _SignalWhenWaiting:
    """Wraps the receiver's queue reader to signal once get() starts waiting for data."""

    def __init__(self, reader, waiting):
        self._reader = reader
        self._waiting = waiting

    def poll(self, timeout=0.0):
        if timeout is None or timeout > 0:
            self._waiting.set()
        return self._reader.poll(timeout)

    def __getattr__(self, name):
        return getattr(self._reader, name)


def idle_consumer(receiver, waiting):
    """Wait for a message that never comes, until a supervisor kills it."""
    queue = receiver._data_queue
    queue._reader = _SignalWhenWaiting(queue._reader, waiting)
    receiver.get(timeout=60)


def interruptible_consumer(receiver, waiting, results):
    """Wait for a message until Ctrl-C arrives, then report it and exit cleanly."""
    queue = receiver._data_queue
    queue._reader = _SignalWhenWaiting(queue._reader, waiting)
    try:
        receiver.get(timeout=60)
    except KeyboardInterrupt:
        results.put("interrupted")


def counting_producer(sender, count, stops):
    """Send numbered payloads, then one stop signal per consumer."""
    with sender:
        for index in range(count):
            sender.put((index, np.full(2_000, index)), timeout=20)
        for _ in range(stops):
            sender.put(None, timeout=20)
        if not sender.wait_for_all_ack(timeout=20):
            raise TimeoutError("Messages were not acknowledged")


def collect_ids(receiver):
    """Consume until a stop signal; return the numbers of intact payloads."""
    handled = []
    while (message := receiver.get(timeout=20)) is not None:
        index, payload = message
        assert (payload == index).all()
        handled.append(index)
    return handled


def id_worker(receiver, results):
    with receiver:
        results.put(collect_ids(receiver))


def partial_consumer(receiver, count, results):
    """Take `count` messages, report them, and exit cleanly, like a worker being recycled."""
    with receiver:
        results.put([receiver.get(timeout=20) for _ in range(count)])


def crashing_consumer(receiver, results, lose_ack):
    """Take one message, then die: before its acknowledgement leaves, or right after."""

    def report_and_exit(*_):
        results.put("received")
        results.close()
        results.join_thread()
        os._exit(17)

    if lose_ack:
        # Die at the exact moment the acknowledgement would be queued.
        receiver._ack_queue.put_nowait = report_and_exit
    receiver.get(timeout=20)
    receiver._ack_queue.close()
    receiver._ack_queue.join_thread()  # The acknowledgement has reached the pipe
    report_and_exit()


inherited_receiver = None  # Set by the parent before it forks


def consume_inherited(count, results):
    """Use a receiver inherited through fork as a module global, not passed as an argument."""
    with inherited_receiver:
        results.put([inherited_receiver.get(timeout=20) for _ in range(count)])


# --- Process pool ---------------------------------------------------------------------------

_pool_receiver = None


def attach_pool_receiver(receiver):
    global _pool_receiver
    _pool_receiver = receiver


def pool_take_one():
    task_id, payload = _pool_receiver.get(timeout=20)
    return task_id, checksum(payload)
