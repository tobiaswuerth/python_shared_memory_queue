import multiprocessing as mp

from .receiver import SharedMemoryReceiver
from .sender import SharedMemorySender


def create_shared_memory_pair(capacity):
    data_queue = mp.Queue()
    ack_queue = mp.Queue()

    sender = SharedMemorySender(capacity, data_queue, ack_queue)
    receiver = SharedMemoryReceiver(data_queue, ack_queue)
    return sender, receiver
