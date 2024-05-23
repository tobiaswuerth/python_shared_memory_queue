# Shared Memory Pipe for Fast Multiprocessing Data Sharing of Large Objects (>1MB)

Since [multiprocessing](https://docs.python.org/3/library/multiprocessing.html) [Queue](https://docs.python.org/3/library/multiprocessing.html#multiprocessing.Queue)s pickles the data, there is a certain performance decrease for larger data objects and huge amounts of I/O.

This is the reason why I created a substitute which instead converts data objects into bytes (if needed), puts the bytes into shared memory and only transfers meta-data about the data structure via Queues to the receiving end.

The following data types are currently supported:
```
- bytes
- int
- float
- bool
- str
- np.ndarray
- np.dtype
```

As well as any nested structures for the given types:
```
- tuple/NamedTuple
- list
- dict
- set
```

# Considerations
There is a certain overhead to the conversion process which is especially noticable for smaller objects and more complex data structures.
Use the following approach depending on the size of the data you're handling:

||10B|100B|1KB|10KB|100KB|1MB|10MB|100MB|1GB|10GB|
|---|---|---|---|---|---|---|---|---|---|---|
|``mp.Queue()``|✅|✅|✅|✅|✅|❌|❌|❌|❌|❌|
|``SharedMemory``|❌|❌|❌|❌|❌|✅|✅|✅|✅|✅|

Actual performance heavily relies on your memory speed.

# Usage example
```python
import multiprocessing as mp

from memory import create_shared_memory_pair, SharedMemorySender, SharedMemoryReceiver

def producer_sm(sender:SharedMemorySender):
    your_data = "your data"

    # ...
    sender.put(your_data) # blocks until there is space

    # or
    if sender.has_space():
        sender.put(your_data)
    else:
        # your else case
    
    # ...
    sender.wait_for_all_ack() # wait for all data to be received before closing the sender
    # exit

def consumer_sm(receiver:SharedMemoryReceiver, loops, size):
    # ...
    data = receiver.get() # blocks
    
    # or
    data = receiver.get(timeout=3) # raises queue.Empty exception after 3s

    # or
    data = receiver.get_nowait()
    if data is None:
        # handle empty case

    # ...

if __name__ == '__main__':
    sender, receiver = create_shared_memory_pair(capacity=5)

    mp.Process(target=producer_sm, args=(sender,)).start()
    mp.Process(target=consumer_sm, args=(receiver,)).start()

```

# Performance Testing
Note that in this testing example Producer and Consumer where dependent upon each other (due to Queue capacity), that's why they take a similar amount of time. Your actual performance may vary depending on the data type, structure and overall system performance.

Since there is no inter-process data transfer, I/O drops accordingly. E.g. in my case -2.4GB/s

```
--------------------
Bytes: 100 (100 B), loops: 100000, queue capacity: 1000
Producer done in     0.78797s  @ 12.10 MB/s
Consumer done in     0.78698s  @ 12.12 MB/s
Producer done in SM  5.37207s  @ 1.78 MB/s
Consumer done in SM  5.37207s  @ 1.78 MB/s   ❌
--------------------
Bytes: 1000 (1000 B), loops: 100000, queue capacity: 1000
Producer done in     0.92900s  @ 102.66 MB/s
Consumer done in     0.91599s  @ 104.11 MB/s
Producer done in SM  5.45683s  @ 17.48 MB/s
Consumer done in SM  5.45582s  @ 17.48 MB/s   ❌
--------------------
Bytes: 10000 (9.77 KB), loops: 100000, queue capacity: 1000
Producer done in     2.16847s  @ 439.79 MB/s
Consumer done in     2.16147s  @ 441.22 MB/s
Producer done in SM  5.64625s  @ 168.90 MB/s 
Consumer done in SM  5.64625s  @ 168.90 MB/s   ❌
--------------------
Bytes: 100000 (97.66 KB), loops: 100000, queue capacity: 1000
Producer done in     2.80213s  @ 3.32 GB/s
Consumer done in     2.80013s  @ 3.33 GB/s
Producer done in SM  8.24400s  @ 1.13 GB/s
Consumer done in SM  8.24200s  @ 1.13 GB/s   ❌
--------------------
Bytes: 1000000 (976.56 KB), loops: 10000, queue capacity: 1000
Producer done in     4.87300s  @ 1.91 GB/s
Consumer done in     4.87300s  @ 1.91 GB/s
Producer done in SM  4.01900s  @ 2.32 GB/s 
Consumer done in SM  4.01800s  @ 2.32 GB/s   ✅
--------------------
Bytes: 10000000 (9.54 MB), loops: 1000, queue capacity: 1000
Producer done in     6.25722s  @ 1.49 GB/s
Consumer done in     6.28221s  @ 1.48 GB/s
Producer done in SM  3.79851s  @ 2.45 GB/s 
Consumer done in SM  3.80359s  @ 2.45 GB/s   ✅
--------------------
Bytes: 100000000 (95.37 MB), loops: 1000, queue capacity: 100
Producer done in     64.91876s  @ 1.43 GB/s
Consumer done in     65.20476s  @ 1.43 GB/s
Producer done in SM  38.08093s  @ 2.45 GB/s
Consumer done in SM  38.17893s  @ 2.44 GB/s   ✅
--------------------
Bytes: 1000000000 (953.67 MB), loops: 100, queue capacity: 10
Producer done in     63.22359s  @ 1.47 GB/s
Consumer done in     66.07801s  @ 1.41 GB/s
Producer done in SM  36.39488s  @ 2.56 GB/s
Consumer done in SM  37.61108s  @ 2.48 GB/s   ✅
--------------------
Bytes: 10000000000 (9.31 GB), loops: 10, queue capacity: 10
Producer done in     CRASHED 
Consumer done in     CRASHED 
Producer done in SM  28.21499s  @ 3.30 GB/s
Consumer done in SM  34.32684s  @ 2.71 GB/s   ✅✅
```