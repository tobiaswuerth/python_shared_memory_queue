import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

import py_sharedmemory

ROOT = Path(__file__).resolve().parents[1]


def test_public_api():
    assert set(py_sharedmemory.__all__) == {
        "SharedMemoryReceiver",
        "SharedMemorySender",
        "__version__",
        "create_shared_memory_pair",
    }
    assert (ROOT / "py_sharedmemory" / "py.typed").is_file()


def test_version_matches_distribution_metadata():
    assert py_sharedmemory.__version__ == version("py-sharedmemory")


@pytest.mark.parametrize(
    ("example", "expected"),
    [
        ("basic.py", "Transferred 10 messages successfully."),
        ("numpy_batches.py", "Received 20 batches of shape (32, 4, 84, 84)."),
        ("worker_pool.py", "3 workers processed 12 tasks."),
    ],
)
def test_examples_run(example, expected):
    result = subprocess.run(
        [sys.executable, str(ROOT / "examples" / example)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
