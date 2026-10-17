import multiprocessing.shared_memory as sm
import warnings
from queue import Queue

import pytest


class LocalQueue(Queue):
    """Synchronous metadata transport for deterministic acknowledgement tests."""

    def close(self):
        pass

    def cancel_join_thread(self):
        pass

    def join_thread(self):
        pass


def assert_unlinked(name):
    try:
        handle = sm.SharedMemory(name=name)
    except FileNotFoundError:
        return
    handle.close()
    pytest.fail(f"Shared-memory block {name} was leaked")


def join_process(process, timeout=15):
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("Child process failed to finish before the deadline")
    assert process.exitcode == 0


def produce(sender, count):
    with sender:
        for index in range(count):
            sender.put((index, bytearray([index % 256]) * 65_536), timeout=10)
        if not sender.wait_for_all_ack(timeout=10):
            raise TimeoutError("Messages were not acknowledged")


def consume(receiver, count, results):
    with receiver:
        values = []
        for _ in range(count):
            index, payload = receiver.get(timeout=10)
            assert payload == bytearray([index % 256]) * 65_536
            values.append(index)
        results.put(values)


PAYLOAD_BYTES = 1_000_000


def make_payload(index):
    """Alternate in-band bytes and out-of-band NumPy buffers with index-specific content."""
    if index % 2:
        return bytes([index % 251]) * PAYLOAD_BYTES
    import numpy as np

    return np.full(PAYLOAD_BYTES // 4, index, dtype=np.int32)


def payload_intact(index, payload):
    if isinstance(payload, bytes):
        return len(payload) == PAYLOAD_BYTES and payload.count(index % 251) == PAYLOAD_BYTES
    return payload.shape == (PAYLOAD_BYTES // 4,) and bool((payload == index).all())


def verify_payloads(receiver, results):
    with receiver:
        while (message := receiver.get(timeout=10)) is not None:
            index, payload = message
            results.put((index, payload_intact(index, payload)))


def exit_with_unreceived_data(sender, ready_to_exit):
    # test_process_exit_with_unread_messages_warns covers the exit warning itself.
    warnings.filterwarnings("ignore", "Closing the sender discards", RuntimeWarning)
    sender.put(bytearray(65_536))
    if not ready_to_exit.wait(10):
        raise TimeoutError("Parent did not read the metadata")
    # Deliberately rely on the process finalizer rather than explicit close.


def abandon_metadata(sender, result_pipe):
    def abandon():
        with sender:
            for _ in range(1_000):
                sender.put(b"x")
            result_pipe.send(list(sender._blocks))
            result_pipe.close()

    with pytest.warns(RuntimeWarning, match="discards 1000 unacknowledged"):
        abandon()


def try_foreign_sender(sender, result_pipe):
    try:
        sender.put("foreign", block=False)
    except RuntimeError as exc:
        result_pipe.send(str(exc))
    else:
        result_pipe.send("unexpected success")
    result_pipe.close()
