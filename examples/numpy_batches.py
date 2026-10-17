"""Stream NumPy batches from a producer process, as in RL rollouts or video decoding.

Requires NumPy (pip install numpy). Each batch is about 3.6 MB of float32 frames.
"""

import multiprocessing as mp

import numpy as np

from py_sharedmemory import SharedMemorySender, create_shared_memory_pair

BATCHES = 20
SHAPE = (32, 4, 84, 84)  # 32 samples of 4 stacked 84x84 frames


def produce(sender: SharedMemorySender) -> None:
    rng = np.random.default_rng(0)
    with sender:
        for step in range(BATCHES):
            batch = {
                "step": step,
                "observations": rng.random(SHAPE, dtype=np.float32),
                "rewards": rng.random(SHAPE[0], dtype=np.float32),
            }
            # Blocks while `capacity` batches are unread, which bounds memory use.
            sender.put(batch, timeout=30)
        sender.put(None, timeout=30)  # End of stream
        if not sender.wait_for_all_ack(timeout=30):
            raise TimeoutError("Consumer did not receive every batch")


def main() -> None:
    ctx = mp.get_context("spawn")
    sender, receiver = create_shared_memory_pair(capacity=4, ctx=ctx)
    process = ctx.Process(target=produce, args=(sender,))
    process.start()
    received = 0
    try:
        with receiver:
            while (batch := receiver.get(timeout=30)) is not None:
                assert batch["step"] == received
                batch["observations"] *= 2  # Received arrays are writable local copies
                received += 1
        process.join(30)
        if process.is_alive() or process.exitcode != 0:
            raise RuntimeError("Producer failed to finish")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        sender.close()
    print(f"Received {received} batches of shape {SHAPE}.")


if __name__ == "__main__":
    main()
