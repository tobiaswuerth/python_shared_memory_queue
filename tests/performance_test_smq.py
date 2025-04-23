import multiprocessing
import time
import json
import os
import numpy as np
from tqdm import tqdm

from memory import create_shared_memory_pair, SharedMemorySender, SharedMemoryReceiver

package_sizes = [
    1_000, # 1KB
    10_000, # 10KB
    100_000, # 100KB
    1_000_000, # 1MB
    10_000_000, # 10MB
    100_000_000, # 100MB
    1_000_000_000, # 1GB
    10_000_000_000, # 10 GB
]
max_memory = 150_000_000_000 # 100 GB
max_tests_per_run = 100_000

out_dir = 'test_results'

def sender_process(size, ready_signal, smq_sender:SharedMemorySender):
    runs = min(max_tests_per_run, max_memory // size)
    print(f"Sender process started with package size: {size} bytes")

    print(f'Sender sending data...')
    time_deltas = []
    for _ in tqdm(range(runs), desc="Sending data", unit="round"):
        data = os.urandom(size)
        start = time.perf_counter()
        smq_sender.put(data)
        end = time.perf_counter()
        time_deltas.append(end - start)

    avg_time = np.mean(time_deltas)
    print(f'Sender finished sending data. Avg: {avg_time:.6f} seconds per {size} bytes')
    
    out_file = os.path.join(out_dir, f'smq_{size:013d}_send.json')
    with open(out_file, 'w') as f:
        json.dump(time_deltas, f)
    print(f'Sender process finished. Data saved to {out_file}')

    ready_signal.set()
    smq_sender.wait_for_all_ack()
    print(f'Sender process exiting...')
    

def receiver_process(size, ready_signal, smq_receiver:SharedMemoryReceiver):
    runs = min(max_tests_per_run, max_memory // size)
    print(f"Receiver process started with package size: {size} bytes")

    print(f'Waiting for sender to finish...')
    ready_signal.wait()
    print(f'Sender is ready. Receiver process starting to receive data...')

    print(f'Receiver receiving data...')
    time_deltas = []
    for _ in tqdm(range(runs), desc="Receiving data", unit="round"):
        start = time.perf_counter()
        data = smq_receiver.get()
        end = time.perf_counter()
        time_deltas.append(end - start)

    avg_time = np.mean(time_deltas)
    print(f'Receiver finished receiving data. Avg: {avg_time:.6f} seconds per {size} bytes')
    
    out_file = os.path.join(out_dir, f'smq_{size:013d}_receive.json')
    with open(out_file, 'w') as f:
        json.dump(time_deltas, f)
    print(f'Receiver process finished. Data saved to {out_file}')
    print(f'Receiver process exiting...')

if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    
    os.makedirs(out_dir, exist_ok=True)

    for size in package_sizes:
        print("-" * 50)
        print(f"Testing with package size: {size} bytes")

        smq_sender, smq_receiver = create_shared_memory_pair(0)
        signal_ready = multiprocessing.Event()

        sender = multiprocessing.Process(target=sender_process, args=(size, signal_ready, smq_sender))
        receiver = multiprocessing.Process(target=receiver_process, args=(size, signal_ready, smq_receiver))

        sender.start()
        receiver.start()
        sender.join()
        receiver.join()

        print(f"Sender and receiver processes completed for package size: {size} bytes")
