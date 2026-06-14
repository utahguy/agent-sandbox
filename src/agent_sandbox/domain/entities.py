"""Domain entities for agent_sandbox.

Pure, framework-free aggregates and result types. Zero imports from
subprocess, click, SQLAlchemy, FastAPI, docker, podman, or any framework.

Entities:
  - SandboxConfig: aggregate root holding all sandbox configuration
  - ExecResult:    immutable result of a container exec command
  - ContainerHandle: reference to a running (or stopped) container
  - ContainerState: enum of container lifecycle states
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from agent_sandbox.domain.value_objects import MemoryLimit, PortMapping, RuntimeKind, Volume


class ContainerState(Enum):
    """Lifecycle state of a managed container.

    Members:
        CREATED: Container has been created but not yet started.
        RUNNING: Container is actively running.
        STOPPED: Container has exited or been stopped.
    """

    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass
class SandboxConfig:
    """Aggregate root that holds all validated sandbox configuration.

    This is the domain aggregate consumed by every use case.  All fields
    have safe defaults so the object can be constructed with no arguments
    (useful for testing and for programmatic construction before loading a
    config file).

    Attributes:
        volumes: Ordered list of bind-mount volumes (1-to-many relation).
        ports: Ordered list of host↔container port mappings (1-to-many).
        env: Frozen mapping of environment variable names to values.
        mise: When True, install tooling via mise on container start.
        memory_limit: Optional memory cap; None means no explicit limit.
        runtime: Which container runtime to use (default: AUTO).
        config_path: Filesystem path of the config file that was parsed
                     (provenance; None when constructed programmatically).
        source_filename: Basename of the config file (e.g. ``.agent-sandbox``).
    """

    volumes: list[Volume] = field(default_factory=list)
    ports: list[PortMapping] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    mise: bool = False
    memory_limit: Optional[MemoryLimit] = None
    runtime: RuntimeKind = RuntimeKind.AUTO
    config_path: Optional[Path] = None
    source_filename: str = ""

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "SandboxConfig":
        """Parse a ``.agent-sandbox`` or ``.claude-sandbox`` config file.

        Wires :class:`~agent_sandbox.infrastructure.file_config_source.FileConfigSource`
        and :class:`~agent_sandbox.application.use_cases.parse_config.ParseConfigUseCase`
        together and sets ``config_path`` / ``source_filename`` provenance fields.

        Args:
            path: Filesystem path to the config file.  Accepts both
                :class:`pathlib.Path` and plain :class:`str`.  The file name
                must be either ``.agent-sandbox`` or ``.claude-sandbox``
                (backward-compat alias).

        Returns:
            A fully validated :class:`SandboxConfig` with provenance fields
            set.

        Raises:
            SandboxError: With code ``CONFIG_MALFORMED`` if the file is not
                found, cannot be read, or contains malformed directives.
        """
        # Late imports to keep the domain layer free of module-level
        # infrastructure coupling while still providing a convenient facade.
        from agent_sandbox.infrastructure.file_config_source import FileConfigSource
        from agent_sandbox.application.use_cases.parse_config import ParseConfigUseCase

        resolved = Path(path)
        source = FileConfigSource(resolved)
        text = source.read_text()

        use_case = ParseConfigUseCase()
        config = use_case.execute(text)

        # Stamp provenance
        config.config_path = resolved
        config.source_filename = resolved.name

        return config


@dataclass(frozen=True)
class ExecResult:
    """Immutable result of executing a command inside a container.

    A non-zero ``exit_code`` is **not** an error — it is faithfully returned
    in this object.  Only ``timed_out=True`` indicates an abnormal termination
    (which also causes a TimeoutError to be raised by the use case layer).

    Attributes:
        exit_code: Process exit code (0 = success, non-zero = failure).
        stdout: Captured standard output of the command.
        stderr: Captured standard error of the command.
        duration_ms: Wall-clock execution time in milliseconds.
        timed_out: True if the command was killed due to a timeout.
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool


@dataclass(frozen=True)
class ContainerHandle:
    """An opaque reference to a managed container instance.

    Passed back to callers after a container is started.  Immutable — the
    infrastructure layer creates a new handle whenever state changes.

    Attributes:
        container_id: Runtime-assigned container identifier (e.g. Docker ID).
        image_tag: Tag of the image the container was launched from.
        runtime: Which runtime is managing this container.
        state: Current lifecycle state of the container.
    """

    container_id: str
    image_tag: str
    runtime: RuntimeKind
    state: ContainerState
