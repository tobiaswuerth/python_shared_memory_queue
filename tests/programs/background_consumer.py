"""A consumer thread in the producing process, ended by program exit or by close().

The shape of an application that hands work to a background thread. With "daemon", a
daemon thread waits in get() without a timeout when the program ends without closing
anything, relying on the exit handlers. With "close-wakes", a regular thread waits in get()
until the main thread closes the receiver, then the sender.

Prints one "ok" line; the program must exit promptly, and anything on stderr is a failure.
"""

import sys
import threading

import numpy as np

from py_sharedmemory import create_shared_memory_pair

MESSAGES = 30


def main(mode):
    sender, receiver = create_shared_memory_pair(capacity=4)
    received = []
    finished = threading.Event()

    def consume():
        try:
            while True:
                index, frame = receiver.get()
                assert (frame == index).all()
                received.append(index)
        except BrokenPipeError:
            finished.set()  # Woken by close()

    consumer = threading.Thread(target=consume, daemon=mode == "daemon")
    consumer.start()
    for index in range(MESSAGES):
        sender.put((index, np.full((240, 320, 3), index, np.uint8)), timeout=10)
    if not sender.wait_for_all_ack(timeout=10):
        raise TimeoutError("The consumer thread did not receive everything")
    assert received == list(range(MESSAGES))
    if mode == "close-wakes":
        receiver.close()
        if not finished.wait(5):
            raise TimeoutError("close() did not wake the consumer thread")
        consumer.join(5)
        sender.close()
    print(f"ok {mode}: {len(received)} messages")
    # "daemon" ends here with the consumer still waiting and both endpoints open.


if __name__ == "__main__":
    main(sys.argv[1])
