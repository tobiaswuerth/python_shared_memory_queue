"""Evaluation rounds: a long-running trainer starts fresh workers and pairs every round.

The shape of periodic evaluation during training. Each round starts a group of spawn
workers, each with its own pair (capacity 1); every worker sends one result, waits for the
acknowledgement and closes, and the parent polls the receivers, joins and closes the
workers, and closes its endpoints. Across many rounds, nothing may accumulate: the program
prints the process's open descriptors (handles on Windows) and, on Linux, its shared-memory
and semaphore names after each round.

Prints "round <n> <descriptors> <shm names>" lines, then "ok"; stderr must stay empty.
"""

import gc
import multiprocessing as mp
import os
import queue
import signal
import sys
import time

import numpy as np

from py_sharedmemory import create_shared_memory_pair

ROUNDS, WORKERS = 8, 2


def open_descriptors():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        count = wintypes.DWORD()
        if not kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count)):
            raise ctypes.WinError(ctypes.get_last_error())
        return count.value
    import fcntl

    count = 0
    for descriptor in range(4096):
        try:
            fcntl.fcntl(descriptor, fcntl.F_GETFD)
        except OSError:
            continue
        count += 1
    return count


def shared_memory_names():
    # Blocks and named semaphores live in /dev/shm on Linux; elsewhere nothing is listed.
    return set(os.listdir("/dev/shm")) if os.path.isdir("/dev/shm") else set()


def evaluate(sender, round_index, worker_index):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        frames = np.full((8, 96, 96, 3), round_index * 10 + worker_index, np.uint8)
        episodes = [
            {"round": round_index, "worker": worker_index, "return": float(n)} for n in range(3)
        ]
        sender.put({"kind": "complete", "frames": frames, "episodes": episodes}, timeout=10)
        if not sender.wait_for_all_ack(timeout=10):
            raise TimeoutError("The trainer did not receive the result")
    finally:
        sender.close()


def run_round(ctx, round_index):
    workers = []
    try:
        for worker_index in range(WORKERS):
            sender, receiver = create_shared_memory_pair(capacity=1, ctx=ctx)
            process = ctx.Process(target=evaluate, args=(sender, round_index, worker_index))
            workers.append({"process": process, "sender": sender, "receiver": receiver})
            process.start()
        results = {}
        deadline = time.monotonic() + 30
        while len(results) < WORKERS:
            for index, worker in enumerate(workers):
                if index in results:
                    continue
                try:
                    results[index] = worker["receiver"].get(timeout=0.1)
                except queue.Empty:
                    if worker["process"].exitcode not in (None, 0):
                        raise RuntimeError(
                            f"Evaluation worker failed: {worker['process'].exitcode}"
                        ) from None
                    if time.monotonic() > deadline:
                        raise TimeoutError("Evaluation timed out") from None
        for index, result in results.items():
            assert result["kind"] == "complete"
            assert (result["frames"] == round_index * 10 + index).all()
            assert [episode["worker"] for episode in result["episodes"]] == [index] * 3
    finally:
        for worker in workers:
            process = worker["process"]
            if process.pid is not None:
                process.join(10)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                    raise RuntimeError("An evaluation worker did not finish")
                if process.exitcode != 0:
                    raise RuntimeError(f"An evaluation worker exited with {process.exitcode}")
            process.close()
            worker["receiver"].close()
            worker["sender"].close()


def main():
    ctx = mp.get_context("spawn")
    names_before = shared_memory_names()
    for round_index in range(ROUNDS):
        run_round(ctx, round_index)
        gc.collect()  # A pair's semaphores are unlinked once its objects are collected
        leftover = len(shared_memory_names() - names_before)
        print(f"round {round_index} {open_descriptors()} {leftover}", flush=True)
    print("ok")


if __name__ == "__main__":
    main()
