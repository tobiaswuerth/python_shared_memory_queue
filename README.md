# Optimized Large-Scale IPC in Python with Shared Memory Queues

[![PyPI version](https://badge.fury.io/py/py-sharedmemory.svg)](https://badge.fury.io/py/py-sharedmemory)

Install using:
```
pip install py-sharedmemory
```

Python's standard `multiprocessing.Queue` relies on `_winapi.CreateFile` for inter-process communication (IPC), introducing significant I/O overhead. This can become a performance bottleneck in demanding applications like Reinforcement Learning, scientific computing, or distributed systems that transfer large amounts of data (e.g. NumPy arrays or tensors) between processes (actors, replay buffers, trainers, etc.).

`py-sharedmemory` provides an alternative that utilizes `multiprocessing.shared_memory` (and therefore `_winapi.CreateFileMapping`) for the main data and sends only lightweight metadata through the queues. This eliminates most inter-process I/O, reducing system load and latency. If you're hitting performance limits with standard queues, `py-sharedmemory` may help.

---

# Usage example
```python
import multiprocessing as mp
from memory import create_shared_memory_pair, SharedMemorySender, SharedMemoryReceiver

def producer_sm(sender:SharedMemorySender):
    your_data = "your data"

    sender.put(your_data) # blocks until space is available
    sender.put(your_data, timeout=3) # raises queue.Full exception after 3s
    sender.put(your_data, block=False) # raises queue.Full exception if no space available
    sender.put_nowait(your_data) # ^ equivalent to above
    # ...

    # wait for all data to be received before closing the sender
    # to properly close all shared memory objects
    sender.wait_for_all_ack()

def consumer_sm(receiver:SharedMemoryReceiver):
    data = receiver.get() # blocks
    data = receiver.get(timeout=3) # raises queue.Empty exception after 3s
    data = receiver.get(block=False) # raises queue.Empty exception if no data available
    data = receiver.get_nowait() # ^ equivalent to above
    # ...

if __name__ == '__main__':
    sender, receiver = create_shared_memory_pair(capacity=5)
    mp.Process(target=producer_sm, args=(sender,)).start()
    mp.Process(target=consumer_sm, args=(receiver,)).start()
```

---

# Considerations
There is a certain overhead to allocating shared memory which is especially noticable for smaller objects.
Use the following heuristic depending on the size of the data you are handling:

||10B|100B|1KB|10KB|100KB|1MB|10MB|100MB|1GB|10GB|
|---|---|---|---|---|---|---|---|---|---|---|
|``mp.Queue()``|✅|✅|✅|✅|✅|❌|❌|❌|❌|❌|
|``py-sharedmemory``|❌|❌|❌|❌|❌|✅|✅|✅|✅|✅|

# Performance Testing

I benchmarked data transfer performance using both the standard `multiprocessing.Queue` and my `py-sharedmemory` implementation:

![Performance Comparison](https://github.com/user-attachments/assets/26143c4d-d2fd-469e-b991-88f5ab0e43f2)

Starting around 1MB per message, `py-sharedmemory` matches or slightly trails the standard queue in speed. However, the key advantage is that it avoids generating I/O, which becomes critical at larger data sizes. Notably, the standard implementation fails with 10GB messages, while `py-sharedmemory` handles them reliably.

Here’s the I/O load on my Windows system using the standard queue:

![I/O for standard implementation](https://github.com/user-attachments/assets/b9a20c06-d3e9-4745-b666-57460f327a6a)

And here’s the I/O load using `py-sharedmemory`:

![SharedMemory I/O](https://github.com/user-attachments/assets/eceb51a6-d876-4d4e-928c-912c2144c49a)

In practice, `py-sharedmemory` delivers smoother and more stable performance, with consistent put/get times and no slowdowns, especially under high data throughput.
