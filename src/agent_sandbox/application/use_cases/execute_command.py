"""Application use case: ExecuteCommandUseCase.

Implements US-006 (FR-005, ADR-005).

Flow:
  1. Accept a ``ContainerHandlePort`` (a live running container reference)
     and a command to execute.
  2. Delegate to ``handle.exec(cmd, timeout=timeout)``.
  3. Return the ``ExecResult`` — non-zero exit codes are NOT errors, they
     are faithfully returned in ``ExecResult.exit_code``.
  4. Let ``TimeoutError`` propagate to the caller unchanged when the command
     exceeds the allowed duration.

Dependency rule: this module imports ONLY from ``agent_sandbox.domain``,
``agent_sandbox.application.ports``, and the Python standard library.
No subprocess, click, docker, podman, sqlalchemy, fastapi, or any
infrastructure import is permitted here.
"""

from __future__ import annotations

from agent_sandbox.application.ports import ContainerHandlePort
from agent_sandbox.domain.entities import ExecResult


class ExecuteCommandUseCase:
    """Execute a command inside a running container and return the result.

    A thin orchestration use case: it delegates directly to the
    :class:`~agent_sandbox.application.ports.ContainerHandlePort` and
    enforces the policy that non-zero exit codes are **not** errors.

    ``TimeoutError`` is **not** caught here — it propagates to the caller
    so that upper layers (the Sandbox facade, the CLI) can decide how to
    handle it.

    Args:
        container_handle: A live container reference satisfying
            :class:`~agent_sandbox.application.ports.ContainerHandlePort`.
            The caller is responsible for ensuring the container is running
            before constructing this use case.
    """

    def __init__(self, container_handle: ContainerHandlePort) -> None:
        self._handle = container_handle

    def execute(
        self,
        cmd: str | list[str],
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute *cmd* inside the container and return an ExecResult.

        A non-zero exit code is **not** an error — it is faithfully returned
        in :attr:`~agent_sandbox.domain.entities.ExecResult.exit_code`.

        Args:
            cmd: Command to run, either as a plain string (split on whitespace)
                or as an argument list (preferred to avoid whitespace ambiguity
                in paths/values).
            timeout: Optional wall-clock timeout in seconds.  ``None`` means
                no explicit timeout.  When exceeded,
                :class:`~agent_sandbox.exceptions.TimeoutError` is raised by
                the underlying adapter.

        Returns:
            An :class:`~agent_sandbox.domain.entities.ExecResult` with
            ``exit_code``, ``stdout``, ``stderr``, ``duration_ms``, and
            ``timed_out`` populated.

        Raises:
            TimeoutError: With code ``EXEC_TIMEOUT`` if the command exceeds
                *timeout* seconds.  This is propagated from the infrastructure
                adapter unchanged.
        """
        return self._handle.exec(cmd, timeout=timeout)
