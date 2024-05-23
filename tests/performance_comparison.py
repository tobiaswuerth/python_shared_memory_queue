import multiprocessing as mp
import time

mp.log_to_stderr()

from memory import create_shared_memory_pair, SharedMemorySender, SharedMemoryReceiver

def create_data(byte_size:int):
    return bytes(byte_size)

def producer_default(sender:mp.Queue, loops, size):
    data = create_data(size)
    t = time.time()
    for _ in range(loops):
        sender.put(data)
    t = time.time() - t

    total_bytes = size * loops
    speed = total_bytes / t
    print(f"Producer done in     {t:.5f}s  @ {byte_to_readable(speed)}/s")

def consumer_default(receiver:mp.Queue, loops, size):
    while receiver.empty():
        pass

    t = time.time()
    for _ in range(loops):
        _ = receiver.get()
    t = time.time() - t

    total_bytes = size * loops
    speed = total_bytes / t
    print(f"Consumer done in     {t:.5f}s  @ {byte_to_readable(speed)}/s")

def producer_sm(sender:SharedMemorySender, loops, size):
    data = create_data(size)
    t = time.time()
    for _ in range(loops):
        sender.put(data)
    t = time.time() - t
    sender.wait_for_all_ack()

    total_bytes = size * loops
    speed = total_bytes / t
    print(f"Producer done in SM  {t:.5f}s  @ {byte_to_readable(speed)}/s")

def consumer_sm(receiver:SharedMemoryReceiver, loops, size):
    while receiver.queue_data_in.empty():
        pass

    t = time.time()
    for _ in range(loops):
        _ = receiver.get()
    t = time.time() - t

    total_bytes = size * loops
    speed = total_bytes / t
    print(f"Consumer done in SM  {t:.5f}s  @ {byte_to_readable(speed)}/s")

def byte_to_readable(byte_size:int):
    if byte_size < 1024:
        return f"{byte_size} B"
    elif byte_size < 1024**2:
        return f"{byte_size/1024:.2f} KB"
    elif byte_size < 1024**3:
        return f"{byte_size/1024**2:.2f} MB"
    elif byte_size < 1024**4:
        return f"{byte_size/1024**3:.2f} GB"
    else:
        return f"{byte_size/1024**4:.2f} TB"

if __name__ == '__main__':

    test_pairs = [
        # (10, 1000000, 1000),
        # (100, 100000, 1000),
        # (100_0, 100000, 1000),
        # (100_00, 100000, 1000),
        (100_000, 100000, 1000),
        (100_000_0, 10000, 1000),
        (100_000_00, 1000, 1000),
        # (100_000_000, 1000, 100),
        # (100_000_000_0, 100, 10),
        # (100_000_000_00, 10, 10),
    ]

    for size, loops, q_size in test_pairs:
        print(f"Bytes: {size} ({byte_to_readable(size)}), loops: {loops}, queue capacity: {q_size}")
        q = mp.Queue(maxsize=5)
        p = mp.Process(target=producer_default, args=(q,loops,size))
        c = mp.Process(target=consumer_default, args=(q,loops,size))
        p.start()
        c.start()
        p.join()
        c.join()

        sender, receiver = create_shared_memory_pair(capacity=5)
        p = mp.Process(target=producer_sm, args=(sender,loops,size))
        c = mp.Process(target=consumer_sm, args=(receiver,loops,size))
        p.start()
        c.start()
        p.join()
        c.join()

        time.sleep(1)

        print("-"*20)

