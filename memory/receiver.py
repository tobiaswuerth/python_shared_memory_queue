from multiprocessing.queues import Queue

from .convert import SMInfo, data_from_smh


class SharedMemoryReceiver:
    def __init__(self, q_data_in: Queue, q_ack_out: Queue):
        self.q_data_in: Queue = q_data_in
        self.q_ack_out: Queue = q_ack_out

    def _process_info(self, info: SMInfo):
        assert info, "No info received"
        data = data_from_smh(info)
        self.q_ack_out.put(info.smh_name)
        del info
        return data

    def get_nowait(self):
        return self.get(block=False)

    def get(self, block: bool = True, timeout: float = None):
        info: SMInfo = self.q_data_in.get(block, timeout)
        return self._process_info(info)
