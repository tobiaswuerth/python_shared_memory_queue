import multiprocessing as mp
import threading as th
import atexit

from .convert import data_to_smh


class SharedMemorySender:
    def __init__(self, capacity: int, q_data_out: mp.Queue, q_ack_in: mp.Queue):
        self.q_data_out: mp.Queue = q_data_out
        self.q_ack_in: mp.Queue = q_ack_in
        self.capacity: int = capacity

        self.is_closed = False
        self.is_initialized = False

        self.open_handles = {}
        self.has_capacity = None
        self.is_empty = None
        self.thread_ack_running = True
        self.thread_ack = None

    def _initialize(self):
        if self.is_initialized:
            return

        self.is_initialized = True
        atexit.register(self._cleanup)

        self.has_capacity = th.Semaphore(self.capacity or 0)
        self.is_empty = th.Event()
        self.is_empty.set()

        self.thread_ack_running = True
        self.thread_ack = th.Thread(target=self._handle_acks, daemon=True)
        self.thread_ack.start()

    def _cleanup(self):
        if self.is_closed is not None:
            self.is_closed = True

        if self.thread_ack_running is not None:
            self.thread_ack_running = False
        if self.thread_ack is not None and self.thread_ack.is_alive():
            self.thread_ack.join(timeout=1.0)

        if self.open_handles is not None:
            for shm_name in list(self.open_handles.keys()):
                try:
                    self._close_handle(shm_name)
                except:
                    pass
            self.open_handles = None

    def __del__(self):
        self._cleanup()

    def _close_handle(self, shm_name: str):
        smh = self.open_handles.pop(shm_name, None)
        if smh is None:
            return
        if len(self.open_handles) == 0:
            self.is_empty.set()

        smh.close()
        smh.unlink()
        if self.capacity:
            self.has_capacity.release()

    def _handle_acks(self):
        while self.thread_ack_running:
            try:
                ack_smh_name = self.q_ack_in.get(timeout=0.1)
                self._close_handle(ack_smh_name)
            except mp.queues.Empty:
                continue
            except Exception as e:
                if not self.is_closed:
                    print(f"SharedMemorySender._handle_acks error: {e}")
                    self._cleanup()
                raise e

    def put_nowait(self, data):
        return self.put(data, block=False)

    def put(self, data, block: bool = True, timeout: float = None):
        if self.is_closed:
            raise BrokenPipeError("Sender is closed.")
        self._initialize()

        if self.capacity:
            if not self.has_capacity.acquire(blocking=block, timeout=timeout):
                raise mp.queues.Full

        try:
            smh, info = data_to_smh(data)
            self.is_empty.clear()
            self.q_data_out.put_nowait(info)
            self.open_handles[smh.name] = smh
        except Exception as e:
            if not self.is_closed:
                print(f"SharedMemorySender.put error: {e}")
                self._cleanup()
            raise e

    def wait_for_all_ack(self):
        if self.is_closed:
            raise BrokenPipeError("Sender is closed.")
        if not self.is_initialized:
            return
        self.is_empty.wait()
