import multiprocessing
import time
import json
import os
import numpy as np
from tqdm import trange


package_sizes = [
    1_000,  # 1KB
    10_000,  # 10KB
    100_000,  # 100KB
    1_000_000,  # 1MB
    10_000_000,  # 10MB
    100_000_000,  # 100MB
    1_000_000_000,  # 1GB
    10_000_000_000,  # 10GB
]
max_memory = 100_000_000_000  # 100 GB
max_tests_per_run = 100_000

out_dir = "test_results"


class PerformanceTest:
    def __init__(self, name, factory: callable):
        self.name = name
        self.factory: callable = factory

    def run(self):
        multiprocessing.set_start_method("spawn", force=True)
        os.makedirs(out_dir, exist_ok=True)

        for size in package_sizes:
            self.test_size(size)

    def test_size(self, size):
        print("-" * 50)
        print(f"Testing with package size: {size} bytes")

        send, receive = self.factory()

        sig_started = multiprocessing.Event()
        sig_sent = multiprocessing.Event()
        sig_done = multiprocessing.Event()

        runs = min(max_tests_per_run, max_memory // size)
        sender = multiprocessing.Process(
            target=sender_process,
            args=(self.name, size, runs, sig_started, sig_sent, sig_done, send),
        )
        receiver = multiprocessing.Process(
            target=receiver_process,
            args=(self.name, size, runs, sig_sent, sig_done, receive),
        )

        receiver.start()
        sender.start()

        sig_started.wait()
        time_sent = time.perf_counter()
        sig_sent.wait()
        time_receive = time.perf_counter()
        time_sent = time_receive - time_sent

        sig_done.wait()
        time_receive = time.perf_counter() - time_receive

        sender.join()
        receiver.join()

        results = {
            "time_sent": time_sent,
            "time_receive": time_receive,
        }
        out_file = os.path.join(out_dir, f"{self.name}_{size:013d}.json")
        with open(out_file, "w") as f:
            json.dump(results, f)

        print(f"Sender and receiver processes completed for package size: {size} bytes")


def dump_results(name, size, suffix, data):
    data = np.array(data)
    stats = {
        "mean": np.mean(data),
        "median": np.median(data),
        "std": np.std(data),
        "min": np.min(data),
        "max": np.max(data),
    }
    out_file = os.path.join(out_dir, f"{name}_{size:013d}_{suffix}.json")
    with open(out_file, "w") as f:
        json.dump(stats, f)


def sender_process(name, size, runs, sig_started, sig_sent, sig_done, send: callable):
    print(f"sender_process started")
    time_deltas = []
    sig_started.set()
    for _ in trange(runs):
        data = os.urandom(size)
        start = time.perf_counter()
        send(data)
        end = time.perf_counter()
        time_deltas.append(end - start)
    sig_sent.set()

    dump_results(name, size, "send", time_deltas)

    sig_done.wait()
    print(f"sender_process exiting...")


def receiver_process(name, size, runs, sig_sent, sig_done, receive: callable):
    print(f"receiver_process started")
    time_deltas = []
    sig_sent.wait()
    for _ in trange(runs):
        start = time.perf_counter()
        data = receive()
        end = time.perf_counter()
        time_deltas.append(end - start)
    sig_done.set()

    dump_results(name, size, "receive", time_deltas)

    print(f"receiver_process exiting...")
