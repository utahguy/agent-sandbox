"""agent_sandbox: Run AI coding agents in isolated, reproducible containers.

Public API:

  - :class:`Sandbox`       — application facade; manages a container session
  - :class:`SandboxConfig` — domain aggregate; validated sandbox configuration
  - :class:`SandboxError`  — root exception for all sandbox failures
  - :class:`TimeoutError`  — raised when a container exec exceeds its timeout
  - :class:`ErrorCode`     — machine-readable error code constants (ADR-005)

Quick start::

    from agent_sandbox import Sandbox, SandboxConfig

    config = SandboxConfig.from_file(".agent-sandbox")  # parse config from disk

    # Explicit lifecycle:
    sandbox = Sandbox(config=config)
    handle = sandbox.start()
    result = handle.exec(["ls", "-la", "/workspace"])
    handle.stop()

    # Context-manager form (preferred):
    with Sandbox(config) as handle:
        result = handle.exec(["echo", "hello"])
"""

from agent_sandbox.exceptions import ErrorCode, SandboxError, TimeoutError
from agent_sandbox.domain.entities import SandboxConfig
from agent_sandbox.facade import Sandbox

__version__ = "0.1.0"

__all__ = [
    # Core classes
    "Sandbox",
    "SandboxConfig",
    # Exceptions
    "SandboxError",
    "TimeoutError",
    "ErrorCode",
    # Metadata
    "__version__",
]
