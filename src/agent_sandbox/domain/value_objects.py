"""Domain value objects for agent_sandbox.

Pure, framework-free typed values. Zero imports from subprocess, click,
SQLAlchemy, FastAPI, docker, podman, or any external framework.

All value objects are immutable (frozen dataclasses). Validation raises
SandboxError(code=CONFIG_MALFORMED) for any invalid input.

Value objects:
  - RuntimeKind: enum of supported container runtimes (AUTO/DOCKER/PODMAN)
  - Volume: bind-mount mapping with mode and selinux label flag
  - PortMapping: host↔container port pair with protocol; range-validated
  - MemoryLimit: memory cap with unit (b/k/m/g)
  - EnvVar: a single environment variable key-value pair (frozen)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent_sandbox.exceptions import ErrorCode, SandboxError


class RuntimeKind(Enum):
    """Supported container runtime kinds.

    AUTO lets the application layer detect the best available runtime.
    DOCKER and PODMAN force a specific runtime.
    """

    AUTO = "AUTO"
    DOCKER = "DOCKER"
    PODMAN = "PODMAN"


@dataclass(frozen=True)
class Volume:
    """A bind-mount volume mapping from host to container.

    Attributes:
        host_path: Absolute path on the host filesystem.
        container_path: Absolute path inside the container.
        mode: Mount mode — ``'ro'`` (read-only) or ``'rw'`` (read-write).
        selinux_relabel: When True, apply the SELinux relabelling flag (``z``).

    Raises:
        SandboxError: If ``mode`` is not ``'ro'`` or ``'rw'``.
    """

    host_path: str
    container_path: str
    mode: str = "rw"
    selinux_relabel: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("ro", "rw"):
            raise SandboxError(
                f"Volume mode must be 'ro' or 'rw', got: {self.mode!r}",
                code=ErrorCode.CONFIG_MALFORMED,
            )


@dataclass(frozen=True)
class PortMapping:
    """A host-to-container port mapping.

    Both host_port and container_port must be in the valid TCP/UDP range
    1–65535 (port 0 is reserved and rejected).

    Attributes:
        host_port: Port number on the host (1–65535).
        container_port: Port number inside the container (1–65535).
        protocol: Transport protocol — ``'tcp'`` (default) or ``'udp'``.

    Raises:
        SandboxError: If any port is outside 1–65535 or protocol is invalid.
    """

    host_port: int
    container_port: int
    protocol: str = "tcp"

    def __post_init__(self) -> None:
        for port, name in [
            (self.host_port, "host_port"),
            (self.container_port, "container_port"),
        ]:
            if not (1 <= port <= 65535):
                raise SandboxError(
                    f"{name} must be in range 1–65535, got: {port}",
                    code=ErrorCode.CONFIG_MALFORMED,
                )
        if self.protocol not in ("tcp", "udp"):
            raise SandboxError(
                f"protocol must be 'tcp' or 'udp', got: {self.protocol!r}",
                code=ErrorCode.CONFIG_MALFORMED,
            )


@dataclass(frozen=True)
class MemoryLimit:
    """A memory cap with a unit suffix.

    Attributes:
        value: Numeric amount (positive integer).
        unit: Unit string — one of ``'b'`` (bytes), ``'k'`` (kibibytes),
              ``'m'`` (mebibytes), ``'g'`` (gibibytes).

    Raises:
        SandboxError: If ``unit`` is not one of the supported values.
    """

    value: int
    unit: str  # b / k / m / g

    _VALID_UNITS = frozenset({"b", "k", "m", "g"})

    def __post_init__(self) -> None:
        if self.unit not in self._VALID_UNITS:
            raise SandboxError(
                f"MemoryLimit unit must be one of b/k/m/g, got: {self.unit!r}",
                code=ErrorCode.CONFIG_MALFORMED,
            )


@dataclass(frozen=True)
class EnvVar:
    """A single environment variable key-value pair.

    Modeled as an atomic frozen value object.  Collections of EnvVar are
    assembled by the SandboxConfig aggregate as a mapping.

    Attributes:
        key: Environment variable name (e.g. ``'API_KEY'``).
        value: Environment variable value (may be empty string).
    """

    key: str
    value: str
