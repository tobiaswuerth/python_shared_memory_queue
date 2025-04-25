import multiprocessing as mp
import queue
import atexit
import threading
import time

from .convert import data_to_smh


class SharedMemorySender:
    def __init__(self, capacity, queue_data_out: mp.Queue, queue_ack_in: mp.Queue):
        self.queue_data_out = queue_data_out
        self.queue_ack_in = queue_ack_in
        self.capacity = capacity

        self.open_handles = {}
        self.open_handles_lock = threading.RLock()
        self.has_capacity = threading.Semaphore(capacity) if capacity else None
        self.thread_ack_running = True
        self.thread_ack = threading.Thread(target=self._handle_acks, daemon=True)
        self.is_empty = threading.Event()
        self.is_empty.set()
        self.thread_ack.start()

        atexit.register(self._cleanup)

    def _cleanup(self):
        if self.thread_ack_running is not None:
            self.thread_ack_running = False

        if self.thread_ack is not None and self.thread_ack.is_alive():
            self.thread_ack.join(timeout=1.0)

        if self.open_handles is not None:
            try:
                for shm_name in list(self.open_handles.keys()):
                    self._close_handle(shm_name)
            except FileNotFoundError | OSError:
                pass
            except Exception as e:
                print(f"Error during cleanup: {e}")
            finally:
                self.open_handles.clear()
                self.open_handles = None

        if self.queue_data_out is not None:
            try:
                self.queue_data_out.close()
            except Exception as e:
                print(f"Error during queue cleanup: {e}")
            finally:
                self.queue_data_out = None

        if self.queue_ack_in is not None:
            try:
                self.queue_ack_in.close()
            except Exception as e:
                print(f"Error during queue cleanup: {e}")
            finally:
                self.queue_ack_in = None

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
                ack_smh_name = self.queue_ack_in.get(timeout=0.1)
                assert ack_smh_name is not None, "Received None as ack_smh_name"
                self._close_handle(ack_smh_name)
            except queue.Empty:
                pass
            except Exception as e:
                print(f"Error in acknowledgement thread: {e}")

    def put(self, data):
        if self.capacity:
            self.has_capacity.acquire()

        smh, info = data_to_smh(data)
        self.queue_data_out.put(info)
        with self.open_handles_lock:
            if len(self.open_handles) == 0:
                self.is_empty.clear()
            self.open_handles[smh.name] = smh

    def has_space(self):
        if not self.capacity:
            return True
        
        if self.has_capacity.acquire(blocking=False):
            self.has_capacity.release()
            return True
        return False

    def wait_for_all_ack(self):
        self.is_empty.wait()
