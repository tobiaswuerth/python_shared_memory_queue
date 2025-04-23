import multiprocessing as mp

from .receiver import SharedMemoryReceiver
from .sender import SharedMemorySender


def create_shared_memory_pair(capacity):
    q_data = mp.Queue(capacity)
    q_ack = mp.Queue()
    sender = SharedMemorySender(capacity, q_data, q_ack)
    receiver = SharedMemoryReceiver(q_data, q_ack)
    return sender, receiver