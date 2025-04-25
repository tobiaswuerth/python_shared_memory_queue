import multiprocessing as mp

from .convert import SMInfo, data_from_smh


class SharedMemoryReceiver:
    def __init__(self, queue_data_in: mp.Queue, queue_ack_out: mp.Queue):
        self.queue_data_in = queue_data_in
        self.queue_ack_out = queue_ack_out

    def _process_info(self, info: SMInfo):
        assert info, "No info received"
        data = data_from_smh(info)
        self.queue_ack_out.put_nowait(info.smh_name)
        del info
        return data

    def get_nowait(self):
        info: SMInfo = self.queue_data_in.get_nowait()
        return self._process_info(info)

    def get(self, block: bool = True, timeout: float = None):
        info: SMInfo = self.queue_data_in.get(block, timeout)
        return self._process_info(info)
