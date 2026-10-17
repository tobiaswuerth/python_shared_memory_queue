"""Shared-memory transport for large picklable Python objects."""

from .factory import create_shared_memory_pair
from .receiver import SharedMemoryReceiver
from .sender import SharedMemorySender

__version__ = "4.0.0"
__all__ = ["SharedMemoryReceiver", "SharedMemorySender", "__version__", "create_shared_memory_pair"]
