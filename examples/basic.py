"""A complete multiprocessing example, using only the standard library."""

import multiprocessing as mp

from py_sharedmemory import SharedMemorySender, create_shared_memory_pair


def produce(sender: SharedMemorySender):
    with sender:
        for index in range(10):
            sender.put({"index": index, "data": bytearray(1_000_000)}, timeout=10)
        if not sender.wait_for_all_ack(timeout=10):
            raise TimeoutError("Consumer did not acknowledge all messages")


def main():
    ctx = mp.get_context("spawn")
    sender, receiver = create_shared_memory_pair(capacity=2, ctx=ctx)
    process = ctx.Process(target=produce, args=(sender,))
    process.start()
    try:
        for index in range(10):
            item = receiver.get(timeout=10)
            assert item["index"] == index
            assert len(item["data"]) == 1_000_000
        process.join(10)
        if process.is_alive() or process.exitcode != 0:
            raise RuntimeError("Producer failed to finish")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        sender.close()
        receiver.close()
    print("Transferred 10 messages successfully.")


if __name__ == "__main__":
    main()
