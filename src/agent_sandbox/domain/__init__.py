"""Domain layer for agent_sandbox.

Pure logic and data — zero framework imports.
No SQLAlchemy, FastAPI, subprocess, click, or HTTP concepts allowed here.

This package exposes the core typed object graph used by all upper layers:
  - value_objects: RuntimeKind, Volume, PortMapping, MemoryLimit, EnvVar
  - entities: SandboxConfig, ExecResult, ContainerHandle, ContainerState
"""

from agent_sandbox.domain.value_objects import (
    EnvVar,
    MemoryLimit,
    PortMapping,
    RuntimeKind,
    Volume,
)
from agent_sandbox.domain.entities import (
    ContainerHandle,
    ContainerState,
    ExecResult,
    SandboxConfig,
)

__all__ = [
    # Value objects
    "RuntimeKind",
    "Volume",
    "PortMapping",
    "MemoryLimit",
    "EnvVar",
    # Entities
    "SandboxConfig",
    "ExecResult",
    "ContainerHandle",
    "ContainerState",
]
