"""Dataset generation: free-running spawn workers, one pair each, collected by polling.

The shape of a parallel data generator. Each worker renders its chunks and sends them
through its own pair (capacity 1), retrying short put() timeouts while checking a stop
event, then waits for its acknowledgements and reports "done" over a status pipe. The
parent polls every receiver with very short timeouts, reassembles the dataset in place,
validates it, and cleans up by joining the workers and closing every endpoint.

With "close-copies", the parent closes its copy of each sender right after starting the
worker that uses it, as it does with the worker's end of the status pipe.

Prints one "ok" line; anything on stderr is a failure.
"""

import multiprocessing as mp
import queue
import signal
import sys
import time
import traceback

import numpy as np

from py_sharedmemory import create_shared_memory_pair

COUNT, SIZE, BATCH, WORKERS = 1_500, 40, 50, 4
WORKER_TIMEOUT = 60


def sample_actions(count, seed):
    return np.random.default_rng(seed).random((count, 9), dtype=np.float32)


def render(actions, size):
    """Deterministic stand-in for rasterizing brush strokes into float16 alpha masks."""
    grid = np.linspace(0, 1, size, dtype=np.float32)
    masks = actions[:, 0, None, None] * grid[None, None, :]
    masks = masks + actions[:, 1, None, None] * grid[None, :, None]
    return (masks / 2).astype(np.float16)


def produce_chunks(sender, status, stop, chunks, size):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        for start, actions in chunks:
            alphas = render(actions, size)
            while not stop.is_set():
                try:
                    sender.put((start, actions, alphas), timeout=0.25)
                    break
                except queue.Full:
                    continue
            else:
                status.send(("stopped", None))
                return
        while not stop.is_set():
            if sender.wait_for_all_ack(timeout=0.25):
                status.send(("done", None))
                return
        status.send(("stopped", None))
    except BaseException:
        status.send(("error", traceback.format_exc()))
    finally:
        try:
            sender.close()
        finally:
            status.close()


def cleanup(records, stop):
    stop.set()
    problems = []
    deadline = time.monotonic() + 10
    for record in records:
        process = record["process"]
        if process is not None and process.pid is not None:
            process.join(timeout=max(0.0, deadline - time.monotonic()))
    for record in records:
        process = record["process"]
        if process is not None and process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(5)
                problems.append(f"Worker {process.pid} required termination")
            if process.exitcode != 0:
                problems.append(f"Worker {process.pid} exited with code {process.exitcode}")
            process.close()
        for key in ("receiver", "sender", "status", "child_status"):
            if record.get(key) is not None:
                record[key].close()
    return problems


def generate(mode):
    actions = sample_actions(COUNT, seed=3)
    alphas = np.empty((COUNT, SIZE, SIZE), np.float16)
    starts = list(range(0, COUNT, BATCH))
    ctx = mp.get_context("spawn")
    stop = ctx.Event()
    records = []
    try:
        for worker_index in range(WORKERS):
            record = {"process": None, "done": False, "last_activity": time.monotonic()}
            records.append(record)
            sender, receiver = create_shared_memory_pair(capacity=1, ctx=ctx)
            record.update(sender=sender, receiver=receiver)
            status, child_status = ctx.Pipe(duplex=False)
            record.update(status=status, child_status=child_status)
            chunks = [
                (start, actions[start : start + BATCH]) for start in starts[worker_index::WORKERS]
            ]
            process = ctx.Process(
                target=produce_chunks,
                args=(sender, child_status, stop, chunks, SIZE),
                name=f"dataset-{worker_index}",
            )
            record["process"] = process
            process.start()
            child_status.close()
            if mode == "close-copies":
                sender.close()

        expected, received = set(starts), set()
        while received != expected or not all(record["done"] for record in records):
            for record in records:
                if record["done"]:
                    continue
                status = record["status"]
                if status.poll():
                    kind, payload = status.recv()
                    record["last_activity"] = time.monotonic()
                    if kind != "done":
                        raise RuntimeError(f"Dataset worker reported {kind}: {payload}")
                    record["done"] = True
                    continue
                try:
                    start, block_actions, block_alphas = record["receiver"].get(timeout=0.01)
                except queue.Empty:
                    # Check the pipe before treating a dead process as an error.
                    exitcode = record["process"].exitcode
                    if exitcode is not None and not status.poll():
                        raise RuntimeError(f"Dataset worker exited early: {exitcode}") from None
                    if time.monotonic() - record["last_activity"] > WORKER_TIMEOUT:
                        raise TimeoutError("Dataset worker made no progress") from None
                    continue
                record["last_activity"] = time.monotonic()
                if start not in expected or start in received:
                    raise RuntimeError(f"Unexpected or duplicate dataset batch {start!r}")
                stop_index = min(start + BATCH, COUNT)
                if not np.array_equal(block_actions, actions[start:stop_index]):
                    raise RuntimeError("Dataset worker returned the wrong actions")
                alphas[start:stop_index] = block_alphas
                received.add(start)
    finally:
        problems = cleanup(records, stop)
    if problems:
        raise RuntimeError("Dataset cleanup failed: " + "; ".join(problems))
    np.testing.assert_array_equal(alphas, render(actions, SIZE))
    return len(received)


if __name__ == "__main__":
    mode = sys.argv[1]
    print(f"ok {mode}: {generate(mode)} batches")
