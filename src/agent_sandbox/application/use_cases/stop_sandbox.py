"""Application use case: StopSandboxUseCase.

Implements US-005 container teardown (ADR-006).

StopSandboxUseCase is responsible for stopping a running container through the
``ContainerPort``.  Idempotency is guaranteed by the
:class:`~agent_sandbox.application.ports.ContainerHandlePort` implementation
(``CliContainerHandle``), which tracks whether the container has already been
stopped and no-ops on subsequent calls.

Dependency rule: this module imports ONLY from ``agent_sandbox.domain``,
``agent_sandbox.application.ports``, and the Python standard library.
No subprocess, click, docker, podman, sqlalchemy, fastapi, or any
infrastructure import is permitted here.
"""

from __future__ import annotations

from agent_sandbox.application.ports import ContainerHandlePort, ContainerPort


class StopSandboxUseCase:
    """Stop and clean up a running container (idempotent).

    Delegates to the injected :class:`~agent_sandbox.application.ports.ContainerPort`
    which in turn calls :meth:`~agent_sandbox.application.ports.ContainerHandlePort.stop`
    on the live container handle.

    Idempotency guarantee:
        Calling ``execute()`` multiple times on the same handle is safe — the
        underlying ``ContainerHandlePort`` implementation uses a ``_stopped``
        flag to turn subsequent calls into no-ops without raising.

    Args:
        container_port: An implementation of
            :class:`~agent_sandbox.application.ports.ContainerPort` (typically
            :class:`~agent_sandbox.infrastructure.container_adapter.CliContainerAdapter`).
    """

    def __init__(self, container_port: ContainerPort) -> None:
        self._container = container_port

    def execute(self, handle: ContainerHandlePort) -> None:
        """Stop and remove the container identified by *handle*.

        Delegates to :meth:`ContainerPort.stop`, which in turn calls
        :meth:`ContainerHandlePort.stop` on the handle.  Idempotent:
        calling this more than once with the same handle is safe.

        Args:
            handle: The live (or already-stopped) container handle returned
                by a previous :class:`StartSandboxUseCase` execution.

        Returns:
            ``None`` — whether via a real stop or an idempotent no-op.
        """
        self._container.stop(handle)
