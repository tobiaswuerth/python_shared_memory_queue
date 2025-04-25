from multiprocessing.connection import PipeConnection
import atexit

from .convert import SMInfo, data_from_smh


class SharedMemoryReceiver:
    def __init__(self, pipe_data_in: PipeConnection, pipe_ack_out: PipeConnection):
        self.pipe_data_in: PipeConnection = pipe_data_in
        self.pipe_ack_out: PipeConnection = pipe_ack_out

        self._closed = False
        atexit.register(self._cleanup)

    def _cleanup(self):
        if self._closed is not None:
            if self._closed:
                return
            self._closed = True

        if self.pipe_data_in is not None:
            try:
                self.pipe_data_in.close()
            except Exception as e:
                print(f"Error during pipe cleanup: {e}")
            finally:
                self.pipe_data_in = None

        if self.pipe_ack_out is not None:
            try:
                self.pipe_ack_out.close()
            except Exception as e:
                print(f"Error during pipe cleanup: {e}")
            finally:
                self.pipe_ack_out = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._cleanup()

    def __del__(self):
        self._cleanup()

    def close(self):
        self._cleanup()

    def _process_info(self, info: SMInfo):
        assert info, "No info received"
        assert not self._closed, "Receiver is closed"

        data = data_from_smh(info)
        self.pipe_ack_out.send(info.smh_name)
        del info
        return data

    def get_nowait(self):
        if self._closed:
            raise BrokenPipeError("Receiver is closed")

        try:
            if not self.pipe_data_in.poll():
                raise EOFError("No data available")

            info: SMInfo = self.pipe_data_in.recv()
            return self._process_info(info)
        except Exception as e:
            print(f"Closing Receiver, Error during get_nowait: {e}")
            self.close()
            raise

    def get(self, timeout: float = None):
        if self._closed:
            raise BrokenPipeError("Receiver is closed")

        try:
            if timeout is not None:
                if not self.pipe_data_in.poll(timeout):
                    raise TimeoutError("Timeout waiting for data")
            else:
                while not self._closed:
                    if self.pipe_data_in.poll(0.1):
                        break

            if self._closed:
                raise BrokenPipeError("Receiver is closed")

            info: SMInfo = self.pipe_data_in.recv()
            return self._process_info(info)
        except Exception as e:
            print(f"Closing Receiver, Error during get: {e}")
            self.close()
            raise
