import multiprocessing as mp
import queue
from multiprocessing.shared_memory import SharedMemory

from .converter import decode, iterables

iterable_keys = set(iterables.values())
non_dict_iterables = set(iterables.keys()) - {dict}

class SharedMemoryReceiver:
    def __init__(self, queue_data_in:mp.Queue, queue_ack_out:mp.Queue):
        self.queue_data_in = queue_data_in
        self.queue_ack_out = queue_ack_out

    def _recover_data_recursive(self, shm:SharedMemory, info:tuple, byte_idx=0):
        if info[0] not in iterable_keys:
            if info[1] == 0: # size
                return decode(None, info)
            return decode(shm.buf[byte_idx:byte_idx+info[1]], info)

        if info[0] == 'd': # dict
            data = {}
            for info_child, key in zip(info[3], info[4]):
                item = self._recover_data_recursive(shm, info_child, byte_idx)
                byte_idx += info_child[1]
                data[key] = item
            return data

        # tuple, list, set
        data = []
        for info_child in info[3]:
            item = self._recover_data_recursive(shm, info_child, byte_idx)
            byte_idx += info_child[1]
            data.append(item)
        
        if info[2] in non_dict_iterables:
            return info[2](data)
        
        # NamedTuple needs to be handled separately
        return info[2](*data)

    def _recover_data(self, info:tuple):
        if info[2] == 0:
            return self._recover_data_recursive(None, info[1:])

        name = info[0]
        info = info[1:]
        shm = SharedMemory(name=name, size=info[1])
        data = self._recover_data_recursive(shm, info)
        shm.close()
        return data

    def get_nowait(self):
        if self.queue_data_in.empty():
            return None
        
        try:
            info = self.queue_data_in.get_nowait()
        except queue.Empty:
            return None
        
        if info is None:
            return None
        
        item = self._recover_data(info)
        self.queue_ack_out.put_nowait(info[0])
        del info
        return item

    def get(self, timeout=None):
        info = self.queue_data_in.get(timeout=timeout)
        item = self._recover_data(info)
        self.queue_ack_out.put_nowait(info[0])
        del info
        return item