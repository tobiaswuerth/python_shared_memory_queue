"""Endpoints of one pair in one process share its queue objects; the last to close them wins.

The creating process holds both endpoints, and a child given both gets them from one pickle,
so each pair of endpoints there shares the same queue objects. Closing those objects ends the
pair for every endpoint of the process, so an endpoint closes them only when no other open
endpoint still uses them, like closing the unused end of a pipe.
"""

from __future__ import annotations

import threading
import weakref
from typing import Any

_lock = threading.Lock()
# Endpoints that are dropped without closing stop counting once they are collected.
_open_endpoints: weakref.WeakKeyDictionary[Any, weakref.WeakSet[Any]] = weakref.WeakKeyDictionary()


def share_queues(queue: Any, endpoint: Any) -> None:
    """Record that an open endpoint uses this process's queue object."""
    with _lock:
        _open_endpoints.setdefault(queue, weakref.WeakSet()).add(endpoint)


def release_queues(queue: Any, endpoint: Any) -> bool:
    """Forget a closing endpoint; whether no other open endpoint still uses the queue."""
    with _lock:
        endpoints = _open_endpoints.get(queue, weakref.WeakSet())
        endpoints.discard(endpoint)
        return not endpoints
