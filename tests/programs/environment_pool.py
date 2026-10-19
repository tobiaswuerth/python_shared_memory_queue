"""Vectorized environment pool: lockstep steps through one pair per spawn worker.

The shape of a reinforcement-learning trainer. The parent keeps every endpoint and sends
commands over a control pipe; each worker steps its environments and answers with one batch
of observations through its own pair (capacity 1). A curriculum command changes the image
size between steps. Shutdown follows the usual pattern: set a stop event, drain the
receivers while the workers finish, join them, then close every pipe and endpoint.

With "stop-early", the training loop fails while two answers per worker are pending, so the
workers are blocked in put() when the shutdown starts.

Prints one "ok" line; anything on stderr is a failure.
"""

import multiprocessing as mp
import queue
import signal
import sys
import time
from contextlib import suppress

import numpy as np

from py_sharedmemory import create_shared_memory_pair

WORKERS = 3
ENVS_PER_WORKER = 2
STEPS = 90
EPISODE_LENGTH = 13
SIZES = {0: 64, 30: 96, 60: 48}  # Curriculum: the image size from this step on


class TrainingFailed(Exception):
    pass


def pixel(seed, step):
    return (seed * 31 + step) % 251


def worker(conn, sender, stop, seeds):
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # The parent handles Ctrl+C
    size, steps = SIZES[0], [0] * len(seeds)
    try:
        while not stop.is_set():
            if not conn.poll(0.1):
                continue
            command, payload = conn.recv()
            if command == "size":
                size, steps = payload, [0] * len(seeds)
                conn.send(("size", size))
                continue
            rows, episodes = [], []
            for index, (seed, action) in enumerate(zip(seeds, payload, strict=True)):
                steps[index] += 1
                done = steps[index] % EPISODE_LENGTH == 0
                if done:
                    episodes.append({"seed": seed, "length": EPISODE_LENGTH})
                rows.append(
                    {
                        "image": np.full((size, size, 3), pixel(seed, steps[index]), np.uint8),
                        "state": np.arange(6, dtype=np.float32) * steps[index],
                        "reward": np.float32(action),
                        "is_last": np.bool_(done),
                    }
                )
            batch = {key: np.stack([row[key] for row in rows]) for key in rows[0]}
            sender.put((batch, episodes), timeout=5)
    except (EOFError, BrokenPipeError):
        pass
    finally:
        try:
            # The parent reads or drains every answer, even when it stops early.
            if not sender.wait_for_all_ack(timeout=10):
                raise TimeoutError("The parent did not receive every answer")
        finally:
            sender.close()
            conn.close()


class EnvironmentPool:
    def __init__(self, ctx):
        self.stop = ctx.Event()
        self.connections, self.receivers, self.senders, self.processes = [], [], [], []
        try:
            for index in range(WORKERS):
                parent, child = ctx.Pipe()
                sender, receiver = create_shared_memory_pair(capacity=1, ctx=ctx)
                seeds = [index * ENVS_PER_WORKER + env for env in range(ENVS_PER_WORKER)]
                process = ctx.Process(
                    target=worker, args=(child, sender, self.stop, seeds), name=f"env-{index}"
                )
                self.connections.append(parent)
                self.receivers.append(receiver)
                self.senders.append(sender)
                self.processes.append(process)
                process.start()
                child.close()
        except BaseException:
            self.close()
            raise

    def check(self):
        for process in self.processes:
            if process.exitcode is not None:
                raise RuntimeError(f"{process.name} exited with code {process.exitcode}")

    def send_actions(self, actions):
        for index, conn in enumerate(self.connections):
            first = index * ENVS_PER_WORKER
            conn.send(("step", actions[first : first + ENVS_PER_WORKER]))

    def collect(self):
        batches, episodes = [], []
        deadline = time.monotonic() + 30
        for receiver in self.receivers:
            while True:
                try:
                    batch, finished = receiver.get(timeout=0.1)
                    break
                except queue.Empty:
                    self.check()
                    if time.monotonic() > deadline:
                        raise TimeoutError("Environment response timeout") from None
            batches.append(batch)
            episodes.extend(finished)
        return {key: np.concatenate([b[key] for b in batches]) for key in batches[0]}, episodes

    def set_size(self, size):
        for conn in self.connections:
            conn.send(("size", size))
        for conn in self.connections:
            if not conn.poll(30):
                raise TimeoutError("Curriculum reset timeout")
            if conn.recv() != ("size", size):
                raise RuntimeError("Unexpected curriculum answer")

    def close(self):
        """Stop the workers, draining their answers so none stays blocked in put()."""
        self.stop.set()
        started = [process for process in self.processes if process.pid is not None]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and any(process.is_alive() for process in started):
            for receiver in self.receivers:
                with suppress(queue.Empty):
                    receiver.get_nowait()
            for process in started:
                process.join(timeout=0.02)
        stragglers = [process.name for process in started if process.is_alive()]
        for process in started:
            if process.is_alive():
                process.terminate()
            process.join(5)
        for conn in self.connections:
            conn.close()
        for receiver in self.receivers:
            receiver.close()
        for sender in self.senders:
            sender.close()
        return stragglers


def verify(batch, episodes, actions, steps, size):
    envs = len(steps)
    assert batch["image"].shape == (envs, size, size, 3), batch["image"].shape
    assert batch["image"].dtype == np.uint8
    for env, step in enumerate(steps):
        assert (batch["image"][env] == pixel(env, step)).all()
        np.testing.assert_array_equal(batch["state"][env], np.arange(6, dtype=np.float32) * step)
    np.testing.assert_array_equal(batch["reward"], actions.astype(np.float32))
    np.testing.assert_array_equal(batch["is_last"], [step % EPISODE_LENGTH == 0 for step in steps])
    finished = sorted(episode["seed"] for episode in episodes)
    assert finished == [env for env, step in enumerate(steps) if step % EPISODE_LENGTH == 0]


def main(mode):
    pool = EnvironmentPool(mp.get_context("spawn"))
    steps = [0] * (WORKERS * ENVS_PER_WORKER)
    completed = 0
    try:
        for step in range(STEPS):
            if step in SIZES:
                pool.set_size(SIZES[step])
                size, steps = SIZES[step], [0] * len(steps)
            actions = np.arange(len(steps)) + step
            pool.send_actions(actions)
            if mode == "stop-early" and step == STEPS // 2:
                pool.send_actions(actions)  # Two answers per worker are now pending
                raise TrainingFailed
            batch, episodes = pool.collect()
            steps = [count + 1 for count in steps]
            verify(batch, episodes, actions, steps, size)
            completed += 1
    except TrainingFailed:
        if mode != "stop-early":
            raise
    finally:
        stragglers = pool.close()
    assert not stragglers, f"Terminated {stragglers}"
    exitcodes = [process.exitcode for process in pool.processes]
    assert exitcodes == [0] * WORKERS, exitcodes
    print(f"ok {mode}: {completed} steps")


if __name__ == "__main__":
    main(sys.argv[1])
