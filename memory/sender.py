import multiprocessing as mp
import queue
from multiprocessing.shared_memory import SharedMemory

from .converter import encode, iterables

class _Separator:
    def __init__(self, obj):
        self.obj = obj

def _iterable_handler_list(stack, parents, entry):
    stack.append(_Separator((type(entry), entry.__class__,)))
    parents.append(([], []))
    for field in reversed(entry): # reverse to maintain order in final binary
        stack.append(field)

def _iterable_handler_set(stack, parents, entry):
    stack.append(_Separator((set,entry.__class__,)))
    parents.append(([], []))
    for field in entry:
        stack.append(field)

def _iterable_handler_dict(stack, parents, entry):
    keys = list(entry.keys())
    stack.append(_Separator((dict,entry.__class__,keys)))
    parents.append(([], []))
    for key in reversed(keys): # reverse to maintain order in final binary
        stack.append(entry[key])

iterable_handlers = {
    list: _iterable_handler_list,
    tuple: _iterable_handler_list,
    set: _iterable_handler_set,
    dict: _iterable_handler_dict,
}

tuple_key = iterables[tuple]

class SharedMemorySender:
    def __init__(self, capacity, queue_data_out:mp.Queue, queue_ack_in:mp.Queue):
        self.open_handles = {}
        self.queue_data_out = queue_data_out
        self.queue_ack_in = queue_ack_in
        self.capacity = capacity

    def _prepare_binary(self, data):
        parents:list[tuple[list[memoryview], list[tuple]]] = []
        stack = [data]
        while stack:
            entry = stack.pop()
            
            if isinstance(entry, _Separator):
                # children processed, gather the results
                bytes_data, child_infos = parents.pop()
                entry_type = entry.obj[0]
                key = iterables.get(entry_type, tuple_key)
                size = sum(info[1] for info in child_infos)
                clazz = entry.obj[1]
                if entry_type == dict:
                    info = (key, size, clazz, child_infos, entry.obj[2])
                else:   
                    info = (key, size, clazz, child_infos)

                if not parents:
                    # root entry was tuple, return the result
                    del parents, stack
                    return info, bytes_data

                # append to parent binary
                parents[-1][0].extend(bytes_data)
                parents[-1][1].append(info)
                continue

            entry_type = type(entry)
            if entry_type in iterables:
                iterable_handlers[entry_type](stack, parents, entry)
                continue
            if isinstance(entry, tuple):
                # NamedTuple
                _iterable_handler_list(stack, parents, entry)
                continue

            info, bytes_data = encode(entry)
            if not parents:
                # root entry was single data, return the result
                del parents, stack
                return info, [bytes_data]

            parents[-1][0].append(bytes_data)
            parents[-1][1].append(info)

    def put(self, data):
        self.process_ack()
        while len(self.open_handles) >= self.capacity:
            self.wait_for_ack()

        info, bytes_datas = self._prepare_binary(data)
        size = info[1]
        if size == 0:
            info = (None, *info)
            self.queue_data_out.put(info)
            return

        shm = SharedMemory(create=True, size=size)
        info = (shm.name, *info)

        offset = 0
        for item in bytes_datas:
            size = len(item)
            shm.buf[offset:offset + size] = item
            offset += size

        self.queue_data_out.put(info)
        self.open_handles[shm.name] = shm

    def close_handle(self, shm_name:str):
        shm = self.open_handles[shm_name]
        shm.close()
        shm.unlink()
        del self.open_handles[shm_name]

    def has_space(self):
        self.process_ack()
        return len(self.open_handles) < self.capacity

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