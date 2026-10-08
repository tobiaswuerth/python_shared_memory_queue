import multiprocessing.shared_memory as sm
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


def exit_with_unreceived_data(sender, ready_to_exit):
    sender.put(bytearray(65_536))
    if not ready_to_exit.wait(10):
        raise TimeoutError("Parent did not read the metadata")
    # Deliberately rely on the process finalizer rather than explicit close.


def abandon_metadata(sender, result_pipe):
    with sender:
        for _ in range(1_000):
            sender.put(b"x")
        result_pipe.send(list(sender._blocks))
        result_pipe.close()


def try_foreign_sender(sender, result_pipe):
    try:
        sender.put("foreign", block=False)
    except RuntimeError as exc:
        result_pipe.send(str(exc))
    else:
        result_pipe.send("unexpected success")
    result_pipe.close()
