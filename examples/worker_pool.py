"""Fan large tasks out to several consumer processes that share one receiver."""

import multiprocessing as mp
import zlib
from multiprocessing.queues import Queue

from py_sharedmemory import SharedMemoryReceiver, create_shared_memory_pair

WORKERS = 3
TASKS = 12


def work(receiver: SharedMemoryReceiver, results: Queue[tuple[int, int]]) -> None:
    with receiver:
        while (task := receiver.get(timeout=30)) is not None:
            task_id, chunk = task
            results.put((task_id, zlib.crc32(chunk)))  # Small results suit a normal queue


def main() -> None:
    ctx = mp.get_context("spawn")
    sender, receiver = create_shared_memory_pair(capacity=2 * WORKERS, ctx=ctx)
    results = ctx.Queue()
    workers = [ctx.Process(target=work, args=(receiver, results)) for _ in range(WORKERS)]
    for worker in workers:
        worker.start()
    try:
        chunks = {task_id: bytes([task_id]) * 2_000_000 for task_id in range(TASKS)}
        with sender:
            for task_id, chunk in chunks.items():
                sender.put((task_id, chunk), timeout=30)  # Each task goes to one worker
            for _ in workers:
                sender.put(None, timeout=30)  # One stop signal per worker
            checksums = dict(results.get(timeout=30) for _ in range(TASKS))
            if not sender.wait_for_all_ack(timeout=30):
                raise TimeoutError("Workers did not receive every task")
        assert checksums == {task_id: zlib.crc32(chunk) for task_id, chunk in chunks.items()}
        for worker in workers:
            worker.join(30)
            if worker.is_alive() or worker.exitcode != 0:
                raise RuntimeError("Worker failed to finish")
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
        sender.close()
        receiver.close()
        results.close()
        results.join_thread()
    print(f"{WORKERS} workers processed {TASKS} tasks.")


if __name__ == "__main__":
    main()
