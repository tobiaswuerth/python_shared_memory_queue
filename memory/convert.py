from typing import NamedTuple
from multiprocessing.shared_memory import SharedMemory
import pickle
from pickle import PickleBuffer


class SMInfo(NamedTuple):
    smh_name: str
    total_bytes: int
    buffer_lengths: list[int]


def data_to_smh(data: any) -> tuple[SharedMemory, SMInfo]:
    buffers: list[PickleBuffer] = []
    buffer_lengths: list[int] = []

    def _append_buffer(buffer: PickleBuffer):
        memview = buffer.raw()
        buffers.append(memview)
        buffer_lengths.append(len(memview))

    main = pickle.dumps(data, pickle.HIGHEST_PROTOCOL, buffer_callback=_append_buffer)

    len_main = len(main)
    total_bytes = len_main + sum(buffer_lengths)
    buffer_lengths.insert(0, len_main)
    buffers.insert(0, main)

    shm: SharedMemory = SharedMemory(create=True, size=total_bytes)
    offset = 0
    for length, buffer in zip(buffer_lengths, buffers):
        shm.buf[offset : offset + length] = buffer
        offset += length

    info: SMInfo = SMInfo(shm.name, total_bytes, buffer_lengths)
    return shm, info


def data_from_smh(info: SMInfo) -> any:
    shm: SharedMemory = SharedMemory(name=info.smh_name, size=info.total_bytes)
    local_buffers: bytes = bytes(shm.buf[: info.total_bytes])
    shm.close()

    # unpack data
    buffers = []
    offset = 0
    for length in info.buffer_lengths:
        buffer = local_buffers[offset : offset + length]
        buffers.append(buffer)
        offset += length

    main = buffers.pop(0)
    return pickle.loads(main, buffers=buffers)
