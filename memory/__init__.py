"""Shared-memory transport for large picklable Python objects."""

from .factory import create_shared_memory_pair
from .receiver import SharedMemoryReceiver
from .sender import SharedMemorySender

__all__ = ["create_shared_memory_pair", "SharedMemorySender", "SharedMemoryReceiver"]
