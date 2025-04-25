from multiprocessing.queues import Queue
from queue import Empty, Full
import atexit
import threading

from .convert import data_to_smh


class SharedMemorySender:
    def __init__(self, capacity: int, q_data_out: Queue, q_ack_in: Queue):
        self.q_data_out: Queue = q_data_out
        self.q_ack_in: Queue = q_ack_in
        self.capacity: int = capacity

        self.is_closed = False
        self.is_initialized = False

        self.open_handles = {}
        self.open_handles_lock = None
        self.has_capacity = None
        self.is_empty = None
        self.thread_ack_running = True
        self.thread_ack = None

    def _initialize(self):
        if self.is_initialized:
            return

        self.is_initialized = True
        atexit.register(self._cleanup)

        self.open_handles_lock = threading.RLock()
        self.has_capacity = threading.Semaphore(self.capacity) if self.capacity else None
        self.is_empty = threading.Event()
        self.is_empty.set()

        self.thread_ack_running = True
        self.thread_ack = threading.Thread(target=self._handle_acks, daemon=True)
        self.thread_ack.start()

    def _cleanup(self):
        if self.is_closed is not None:
            self.is_closed = True
        if self.thread_ack_running is not None:
            self.thread_ack_running = False

        if self.thread_ack is not None and self.thread_ack.is_alive():
            try:
                self.thread_ack.join(timeout=1.0)
            except:
                pass

        if self.open_handles is not None:
            for shm_name in list(self.open_handles.keys()):
                try:
                    self._close_handle(shm_name)
                except:
                    pass
            self.open_handles.clear()
            self.open_handles = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._cleanup()

    def __del__(self):
        self._cleanup()

    def close(self):
        self._cleanup()

    def _close_handle(self, shm_name: str):
        with self.open_handles_lock:
            shm = self.open_handles[shm_name]
            shm.close()
            shm.unlink()
            del self.open_handles[shm_name]
            if len(self.open_handles) == 0:
                self.is_empty.set()
            if self.capacity:
                self.has_capacity.release()

    def _handle_acks(self):
        while self.thread_ack_running:
            try:
                ack_smh_name = self.q_ack_in.get(timeout=0.1)
                self._close_handle(ack_smh_name)
            except Empty:
                continue
            except Exception as e:
                if not self.is_closed:
                    self.close()
                break

    def put_nowait(self, data):
        if self.is_closed:
            raise BrokenPipeError("Sender is closed.")
        self._initialize()

        if not self.has_space():
            raise Full

        try:
            smh, info = data_to_smh(data)
            self.q_data_out.put_nowait(info)
            with self.open_handles_lock:
                if len(self.open_handles) == 0:
                    self.is_empty.clear()
                self.open_handles[smh.name] = smh
        except Exception as e:
            print(f"Error during put_nowait: {e}")
            self.close()
            raise e

    def put(self, data, timeout: float = None):
        if self.is_closed:
            raise BrokenPipeError("Sender is closed.")
        self._initialize()

        if self.capacity:
            if timeout is None:
                while not self.is_closed:
                    if self.has_capacity.acquire(timeout=0.1):
                        break
            else:
                if not self.has_capacity.acquire(timeout=timeout):
                    raise Full

        if self.is_closed:
            raise BrokenPipeError("Sender is closed.")

        try:
            smh, info = data_to_smh(data)
            self.q_data_out.put_nowait(info)

            with self.open_handles_lock:
                if len(self.open_handles) == 0:
                    self.is_empty.clear()
                self.open_handles[smh.name] = smh
        except Exception as e:
            print(f"Error during put: {e}")
            self.close()
            raise e

    def has_space(self):
        if self.is_closed:
            raise BrokenPipeError("Sender is closed.")

        if not self.capacity or not self.is_initialized:
            return True

        if self.has_capacity.acquire(blocking=False):
            self.has_capacity.release()
            return True
        return False

    def wait_for_all_ack(self):
        if self.is_closed:
            raise BrokenPipeError("Sender is closed.")
        if not self.is_initialized:
            return
        self.is_empty.wait()
