"""Application-layer port interfaces (ADR-001).

Each port is a ``typing.Protocol`` so that use cases stay I/O-free and can be
unit-tested with lightweight fakes instead of real infrastructure adapters.

Dependency rule: this module imports ONLY from ``agent_sandbox.domain`` and
the Python standard library (``typing``, ``__future__``).
No subprocess, click, docker, podman, SQLAlchemy, FastAPI, or any
infrastructure import is permitted here.

Ports defined:
  - ConfigSourcePort   — read raw configuration text from any source
  - RuntimePort        — detect and invoke the container runtime CLI
  - ImageBuilderPort   — build container images and query the local cache
  - ContainerHandlePort — abstract interface for a live container (exec/stop)
  - ContainerPort      — manage container lifecycle (start/exec/stop)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_sandbox.domain.entities import ExecResult, SandboxConfig
from agent_sandbox.domain.value_objects import RuntimeKind

# Re-export for convenience
__all__ = [
    "ConfigSourcePort",
    "RuntimePort",
    "ImageBuilderPort",
    "ContainerHandlePort",
    "ContainerPort",
]


@runtime_checkable
class ConfigSourcePort(Protocol):
    """Port for reading raw configuration text from any backing source.

    Implementations might read from a file on disk, from an environment
    variable, from a network location, or from an in-memory string (for tests).

    The returned text is subsequently parsed by the ParseConfigUseCase.
    """

    def read_text(self) -> str:
        """Return the raw configuration text.

        Returns:
            The full text of the configuration source (e.g. the contents of a
            ``.agent-sandbox`` file).
        """
        ...


@runtime_checkable
class RuntimePort(Protocol):
    """Port for detecting the available container runtime and running its CLI.

    Implementations wrap ``docker`` or ``podman`` command-line invocations.
    Use cases interact with this port without any knowledge of subprocesses.
    """

    def detect(self) -> RuntimeKind:
        """Detect which container runtime is available on the host.

        Returns:
            The detected :class:`~agent_sandbox.domain.value_objects.RuntimeKind`
            (``DOCKER`` or ``PODMAN``).

        Raises:
            SandboxError: With code ``RUNTIME_NOT_FOUND`` if neither Docker nor
                Podman is found on ``PATH``.
        """
        ...

    def run_cli(
        self,
        args: list[str],
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        """Run the container runtime CLI with the given arguments.

        Args:
            args: Argument list passed to the runtime binary (e.g.
                ``["run", "--rm", "ubuntu:22.04", "echo", "hi"]``).
            timeout: Optional wall-clock timeout in seconds.  ``None`` means
                no explicit timeout.

        Returns:
            A 3-tuple ``(exit_code, stdout, stderr)`` where ``exit_code`` is
            the process return code and ``stdout``/``stderr`` are the captured
            output strings.

        Raises:
            SandboxError: On unexpected runtime failures (not non-zero exit).
            TimeoutError: If the command exceeds ``timeout`` seconds.
        """
        ...


@runtime_checkable
class ImageBuilderPort(Protocol):
    """Port for building container images and querying the local image cache.

    Implementations use the container runtime (Docker or Podman) to build
    images from a Containerfile and check whether a given image tag already
    exists in the local cache.
    """

    def is_cached(self, image_tag: str) -> bool:
        """Check whether an image with the given tag is in the local cache.

        Args:
            image_tag: The fully-qualified image tag (e.g.
                ``"agent-sandbox:sha256-abc123"``).

        Returns:
            ``True`` if the image already exists locally, ``False`` otherwise.
        """
        ...

    def ensure_image(self, image_tag: str, containerfile_content: str) -> None:
        """Build and cache the image if it is not already present.

        If :meth:`is_cached` returns ``True`` for ``image_tag``, this method
        is a no-op.  Otherwise it builds the image from ``containerfile_content``
        and tags it as ``image_tag``.

        Args:
            image_tag: Target tag for the built image.
            containerfile_content: Full text of the Containerfile used to build
                the image.

        Raises:
            SandboxError: With code ``IMAGE_BUILD_FAILED`` if the build fails.
        """
        ...


@runtime_checkable
class ContainerHandlePort(Protocol):
    """Protocol for an active running container that supports exec and stop.

    This is the abstract "live container" reference returned by
    :meth:`ContainerPort.start`.  Infrastructure adapters implement this
    protocol.  Unlike the domain entity
    :class:`~agent_sandbox.domain.entities.ContainerHandle` (which is an
    immutable data bag), ``ContainerHandlePort`` carries behaviour (exec/stop).

    Attributes:
        container_id: Runtime-assigned container identifier.
        image_tag: Tag of the image the container was launched from.
    """

    @property
    def container_id(self) -> str:
        """Runtime-assigned container identifier."""
        ...

    @property
    def image_tag(self) -> str:
        """Tag of the image the container was launched from."""
        ...

    def exec(
        self,
        cmd: str | list[str],
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute a command inside the running container.

        A non-zero exit code is **not** an error — it is faithfully returned
        in :attr:`~agent_sandbox.domain.entities.ExecResult.exit_code`.

        Args:
            cmd: Command to run, either as a shell string or an argument list.
            timeout: Optional wall-clock timeout in seconds.

        Returns:
            An :class:`~agent_sandbox.domain.entities.ExecResult` with
            ``exit_code``, ``stdout``, ``stderr``, ``duration_ms``, and
            ``timed_out``.

        Raises:
            TimeoutError: If ``timeout`` is exceeded.
        """
        ...

    def stop(self) -> None:
        """Stop and remove the container (idempotent).

        Guarantees no orphaned container remains after this call returns.
        Calling ``stop()`` on an already-stopped container must not raise.
        """
        ...


@runtime_checkable
class ContainerPort(Protocol):
    """Port for managing the full container lifecycle (start / exec / stop).

    Implementations wrap the container runtime CLI to create, interact with,
    and tear down isolated containers.

    Use cases depend on this port and are therefore free of subprocess
    knowledge.
    """

    def start(
        self,
        config: SandboxConfig,
        image_tag: str,
    ) -> ContainerHandlePort:
        """Start a new isolated container and return a handle to it.

        Applies ``config`` (volumes, ports, env, memory limit, runtime) when
        creating the container.

        Args:
            config: Validated sandbox configuration aggregate.
            image_tag: Tag of the pre-built image to launch.

        Returns:
            A :class:`ContainerHandlePort` representing the live container.

        Raises:
            SandboxError: With code ``CONTAINER_START_FAILED`` if the runtime
                fails to start the container.
        """
        ...

    def exec(
        self,
        handle: ContainerHandlePort,
        cmd: str | list[str],
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute a command in a running container identified by ``handle``.

        Args:
            handle: The live container reference returned by :meth:`start`.
            cmd: Command to run inside the container.
            timeout: Optional wall-clock timeout in seconds.

        Returns:
            An :class:`~agent_sandbox.domain.entities.ExecResult`.

        Raises:
            TimeoutError: If the command exceeds ``timeout`` seconds.
        """
        ...

    def stop(self, handle: ContainerHandlePort) -> None:
        """Stop and remove the container identified by ``handle`` (idempotent).

        Args:
            handle: The live container reference to stop.
        """
        ...
