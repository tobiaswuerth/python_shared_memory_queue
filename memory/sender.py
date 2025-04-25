import multiprocessing as mp
import queue
from multiprocessing.shared_memory import SharedMemory
import pickle
from pickle import PickleBuffer

class SharedMemorySender:
    def __init__(self, capacity, queue_data_out:mp.Queue, queue_ack_in:mp.Queue):
        self.open_handles = {}
        self.queue_data_out = queue_data_out
        self.queue_ack_in = queue_ack_in
        self.capacity = capacity

    def _prepare_binary(self, data):
        buffers:list[PickleBuffer] = []
        buffer_lengths:list[int] = []
        def _append_buffer(buffer:PickleBuffer):
            memview = buffer.raw()
            buffers.append(memview)
            buffer_lengths.append(len(memview))
        
        main = pickle.dumps(data, buffer_callback=_append_buffer, protocol=pickle.HIGHEST_PROTOCOL)

        len_main = len(main)
        total = len_main + sum(buffer_lengths)
        buffer_lengths.insert(0, len_main)
        buffers.insert(0, main)

        return total, buffer_lengths, buffers


    def put(self, data):
        self.process_ack()
        while self.capacity and len(self.open_handles) >= self.capacity:
            self.wait_for_ack()

        total_bytes, buffer_lengths, buffers = self._prepare_binary(data)
        assert total_bytes > 0, 'No data to send'

        shm = SharedMemory(create=True, size=total_bytes)
        info = ((shm.name, total_bytes), buffer_lengths)

        offset = 0
        for length, buffer in zip(buffer_lengths, buffers):
            shm.buf[offset:offset + length] = buffer
            offset += length

        self.queue_data_out.put(info)
        self.open_handles[shm.name] = shm

    def close_handle(self, shm_name:str):
        shm = self.open_handles[shm_name]
        shm.close()
        shm.unlink()
        del self.open_handles[shm_name]

    def has_space(self):
        self.process_ack()
        return not self.capacity or len(self.open_handles) < self.capacity

    def process_ack(self):
        try:
            while True:
                retour = self.queue_ack_in.get_nowait()
                if retour is None:
                    return
                self.close_handle(retour)
        except queue.Empty:
            pass

    def close_all(self):
        for shm_name in list(self.open_handles.keys()):
            self.close_handle(shm_name)

    def wait_for_ack(self):
        shm_name = self.queue_ack_in.get()
        self.close_handle(shm_name)

    def wait_for_all_ack(self):
        while len(self.open_handles) > 0:
            self.wait_for_ack()

    def __del__(self):
        self.close_all()