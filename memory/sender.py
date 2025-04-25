import multiprocessing as mp
from multiprocessing.connection import PipeConnection
import atexit
import threading

from .convert import data_to_smh


class SharedMemorySender:
    def __init__(self, capacity: int, pipe_data_out: PipeConnection, pipe_ack_in: PipeConnection):
        self.pipe_data_out: PipeConnection = pipe_data_out
        self.pipe_ack_in: PipeConnection = pipe_ack_in
        self.is_closed = False

        self.capacity: int = capacity

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

        if self.pipe_data_out is not None:
            try:
                self.pipe_data_out.close()
            except Exception as e:
                print(f"Error during pipe cleanup: {e}")
            finally:
                self.pipe_data_out = None

        if self.pipe_ack_in is not None:
            try:
                self.pipe_ack_in.close()
            except Exception as e:
                print(f"Error during pipe cleanup: {e}")
            finally:
                self.pipe_ack_in = None

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
                if self.pipe_ack_in.poll(0.1):
                    ack_smh_name = self.pipe_ack_in.recv()
                    assert ack_smh_name is not None, "Received None as ack_smh_name"
                    self._close_handle(ack_smh_name)
            except Exception as e:
                if not self.is_closed:
                    self.close()
                break

    def put_nowait(self, data):
        if self.is_closed:
            raise BrokenPipeError("Sender is closed.")

        if not self.has_space():
            raise BlockingIOError("No space available in the sender.")

        try:
            smh, info = data_to_smh(data)
            self.pipe_data_out.send(info)
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

        try:
            if self.capacity:
                if timeout is not None:
                    if not self.has_capacity.acquire(timeout=timeout):
                        raise TimeoutError("Timeout waiting for space in the sender.")
                else:
                    while not self.is_closed:
                        if self.has_capacity.acquire(timeout=0.1):
                            break

            if self.is_closed:
                raise BrokenPipeError("Sender is closed.")

            smh, info = data_to_smh(data)
            self.pipe_data_out.send(info)
            with self.open_handles_lock:
                if len(self.open_handles) == 0:
                    self.is_empty.clear()
                self.open_handles[smh.name] = smh
        except Exception as e:
            print(f"Error during put: {e}")
            self.close()
            raise e

    def has_space(self):
        if not self.capacity:
            return True

        if self.has_capacity.acquire(blocking=False):
            self.has_capacity.release()
            return True
        return False

    def wait_for_all_ack(self):
        self.is_empty.wait()
