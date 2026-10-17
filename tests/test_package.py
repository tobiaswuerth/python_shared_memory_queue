import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import memory

ROOT = Path(__file__).resolve().parents[1]


def test_public_api():
    assert set(memory.__all__) == {
        "SharedMemoryReceiver",
        "SharedMemorySender",
        "__version__",
        "create_shared_memory_pair",
    }
    assert (ROOT / "memory" / "py.typed").is_file()


def test_version_matches_distribution_metadata():
    assert memory.__version__ == version("py-sharedmemory")


def test_basic_example_runs():
    result = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "basic.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Transferred 10 messages successfully." in result.stdout
