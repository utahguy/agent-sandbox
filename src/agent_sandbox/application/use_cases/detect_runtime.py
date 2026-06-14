"""Application use case: DetectRuntimeUseCase.

Selects the appropriate container runtime based on :class:`SandboxConfig`
and the available runtimes reported by the :class:`RuntimePort`.

Dependency rule: this module imports ONLY from ``agent_sandbox.domain``,
``agent_sandbox.application.ports``, and the Python standard library.
No subprocess, click, docker, podman, sqlalchemy, fastapi, or any
infrastructure import is permitted here.

The preference resolution order when ``runtime=AUTO``:
  1. Podman (rootless by default — security-preferred per ADR-003)
  2. Docker (falls back if Podman is unavailable)

Explicit ``runtime=DOCKER`` or ``runtime=PODMAN`` bypasses auto-detection
and is honored directly (the port/adapter validates availability).
"""

from __future__ import annotations

from agent_sandbox.application.ports import RuntimePort
from agent_sandbox.domain.entities import SandboxConfig
from agent_sandbox.domain.value_objects import RuntimeKind


class DetectRuntimeUseCase:
    """Select a container runtime from config and available runtimes.

    The use case itself holds no subprocess knowledge — all I/O is
    delegated to the injected :class:`~agent_sandbox.application.ports.RuntimePort`.

    Usage::

        adapter = SubprocessRuntimeAdapter(preferred=config.runtime)
        use_case = DetectRuntimeUseCase(runtime_port=adapter)
        chosen_runtime = use_case.execute(config)

    Args:
        runtime_port: An implementation of :class:`RuntimePort` (typically
            :class:`~agent_sandbox.infrastructure.subprocess_runtime.SubprocessRuntimeAdapter`).
    """

    def __init__(self, runtime_port: RuntimePort) -> None:
        self._port = runtime_port

    def execute(self, config: SandboxConfig) -> RuntimeKind:
        """Detect and return the runtime to use for the given config.

        Delegates to :meth:`RuntimePort.detect` which encapsulates the
        preference logic (AUTO → Podman-first; explicit → honour as-is).

        Args:
            config: Validated sandbox configuration aggregate.  The
                ``config.runtime`` field carries the user's preference
                (``AUTO``, ``DOCKER``, or ``PODMAN``).

        Returns:
            The resolved :class:`~agent_sandbox.domain.value_objects.RuntimeKind`
            (always ``DOCKER`` or ``PODMAN``, never ``AUTO``).

        Raises:
            SandboxError: With code ``RUNTIME_NOT_FOUND`` if the requested
                (or any, in AUTO mode) runtime is not found on ``PATH``.
        """
        return self._port.detect()
