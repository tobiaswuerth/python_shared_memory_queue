import multiprocessing as mp

from .receiver import SharedMemoryReceiver
from .sender import SharedMemorySender


def create_shared_memory_pair(capacity):
    data_recv_end, data_send_end = mp.Pipe(duplex=False)
    ack_recv_end, ack_send_end = mp.Pipe(duplex=False)
    
    sender = SharedMemorySender(capacity, data_send_end, ack_recv_end)
    receiver = SharedMemoryReceiver(data_recv_end, ack_send_end)
    return sender, receiver