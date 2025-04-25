import multiprocessing as mp
import queue
import atexit

from .convert import data_to_smh

class SharedMemorySender:
    def __init__(self, capacity, queue_data_out:mp.Queue, queue_ack_in:mp.Queue):
        self.queue_data_out = queue_data_out
        self.queue_ack_in = queue_ack_in
        self.capacity = capacity
        
        self.open_handles = {}

        atexit.register(self._cleanup)

    def _cleanup(self):
        if hasattr(self, 'open_handles') and self.open_handles is not None:
            try:
                for shm_name in list(self.open_handles.keys()):
                    shm = self.open_handles[shm_name]
                    shm.close()
                    shm.unlink()
                    del self.open_handles[shm_name]
            except FileNotFoundError | OSError:
                pass
            except Exception as e:
                print(f"Error during cleanup: {e}")
            finally:
                self.open_handles.clear()
                self.open_handles = None

        if hasattr(self, 'queue_data_out') and self.queue_data_out is not None:
            try:
                self.queue_data_out.close()
            except Exception as e:
                print(f"Error during queue cleanup: {e}")
            finally:
                self.queue_data_out = None

        if hasattr(self, 'queue_ack_in') and self.queue_ack_in is not None:
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


    def put(self, data):
        self.process_ack()
        while self.capacity and len(self.open_handles) >= self.capacity:
            self.wait_for_ack()

        smh, info = data_to_smh(data)
        self.queue_data_out.put(info)
        self.open_handles[smh.name] = smh

    def close_handle(self, shm_name:str):
        shm = self.open_handles[shm_name]
        shm.close()
        shm.unlink()
        del self.open_handles[shm_name]

    def has_space(self):
        self.process_ack()
        return not self.capacity or len(self.open_handles) < self.capacity

    def process_ack(self):
        try:
            while True:
                retour = self.queue_ack_in.get_nowait()
                if retour is None:
                    return
                self.close_handle(retour)
        except queue.Empty:
            pass

    def wait_for_ack(self):
        shm_name = self.queue_ack_in.get()
        self.close_handle(shm_name)

    def wait_for_all_ack(self):
        while len(self.open_handles) > 0:
            self.wait_for_ack()
