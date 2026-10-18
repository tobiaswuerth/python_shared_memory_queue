"""Shared-memory queues for large picklable Python objects and NumPy arrays.

Payloads travel through shared memory; the multiprocessing queues underneath carry only
small metadata. Usage mirrors `multiprocessing.Queue`::

    import multiprocessing as mp
    from py_sharedmemory import create_shared_memory_pair

    def produce(sender):
        with sender:
            sender.put(bytearray(16_000_000))
            sender.wait_for_all_ack()

    if __name__ == "__main__":
        sender, receiver = create_shared_memory_pair(capacity=2)
        worker = mp.Process(target=produce, args=(sender,))
        worker.start()
        with receiver:
            frames = receiver.get(timeout=30)
        worker.join()
        sender.close()
"""

from .factory import create_shared_memory_pair
from .receiver import SharedMemoryReceiver
from .sender import SharedMemorySender

__version__ = "4.3.0"
__all__ = ["SharedMemoryReceiver", "SharedMemorySender", "__version__", "create_shared_memory_pair"]
