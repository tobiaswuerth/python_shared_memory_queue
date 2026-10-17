# Security policy

## Supported versions

Security fixes are released for the latest version on
[PyPI](https://pypi.org/project/py-sharedmemory/).

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub's private vulnerability reporting](https://github.com/tobiaswuerth/python_shared_memory_queue/security/advisories/new)
rather than in a public issue. Include the affected version, platform, and steps to
reproduce. You should receive a response within a week.

## Trust model

Messages are serialized with `pickle`, and unpickling data can run arbitrary code. Only
exchange messages between processes you trust, as with `multiprocessing.Queue`. On Linux
and macOS, shared-memory blocks are created readable and writable only by the current
user.
