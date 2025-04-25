import multiprocessing as mp
from multiprocessing.shared_memory import SharedMemory

import pickle


class SharedMemoryReceiver:
    def __init__(self, queue_data_in: mp.Queue, queue_ack_out: mp.Queue):
        self.queue_data_in = queue_data_in
        self.queue_ack_out = queue_ack_out

    def _recover_data(self, info: tuple):
        # get data
        name, total_bytes = info[0]
        shm = SharedMemory(name=name, size=total_bytes)
        local_buffers = bytes(shm.buf[:total_bytes])
        shm.close()

        # unpack data
        buffers = []
        offset = 0
        for length in info[1]:
            buffer = local_buffers[offset : offset + length]
            buffers.append(buffer)
            offset += length

        main = buffers.pop(0)
        return pickle.loads(main, buffers=buffers)

    def _process_info(self, info: tuple):
        assert info, "No info received"
        item = self._recover_data(info)
        self.queue_ack_out.put_nowait(info[0][0])
        del info
        return item

    def get_nowait(self):
        info = self.queue_data_in.get_nowait()
        return self._process_info(info)

    def get(self, timeout=None):
        info = self.queue_data_in.get(timeout=timeout)
        return self._process_info(info)
