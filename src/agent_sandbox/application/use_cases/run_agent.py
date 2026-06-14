"""Application use case: RunAgentUseCase.

Orchestrates the full agent-in-sandbox thread (US-007, FR-016):
  config → ensure image → start container → execute agent command → stop/cleanup.

This use case composes existing lower-level use cases:
  - StartSandboxUseCase   (ensures image + starts container)
  - ExecuteCommandUseCase (runs the agent command inside the container)
  - StopSandboxUseCase    (stops and removes the container)

The container is **always** stopped in a ``try/finally`` block, guaranteeing
cleanup on:
  - Normal exit (command completes successfully or with non-zero exit code)
  - Exception   (SandboxError, TimeoutError, or any unexpected error)
  - SIGINT      (KeyboardInterrupt raised by Ctrl-C)

Dependency rule: this module imports ONLY from ``agent_sandbox.domain``,
``agent_sandbox.application.ports``, other application use cases, and the
Python standard library.  No subprocess, click, docker, podman,
sqlalchemy, fastapi, or any infrastructure import is permitted here.
"""

from __future__ import annotations

from typing import Callable

from agent_sandbox.domain.entities import ExecResult, SandboxConfig
from agent_sandbox.domain.image_spec import ImageSpec


class RunAgentUseCase:
    """Orchestrate the full run: image → start container → exec agent → stop.

    Composes :class:`~agent_sandbox.application.use_cases.start_sandbox.StartSandboxUseCase`,
    :class:`~agent_sandbox.application.use_cases.execute_command.ExecuteCommandUseCase`,
    and :class:`~agent_sandbox.application.use_cases.stop_sandbox.StopSandboxUseCase`
    behind a single ``execute()`` call that guarantees cleanup via ``try/finally``.

    Args:
        start_sandbox_use_case: Pre-built
            :class:`~agent_sandbox.application.use_cases.start_sandbox.StartSandboxUseCase`
            (or duck-typed equivalent) with its adapters already wired.
        stop_sandbox_use_case: Pre-built
            :class:`~agent_sandbox.application.use_cases.stop_sandbox.StopSandboxUseCase`
            (or duck-typed equivalent) with its adapters already wired.
        execute_command_use_case_factory: Optional callable that accepts a
            :class:`~agent_sandbox.application.ports.ContainerHandlePort` and
            returns an
            :class:`~agent_sandbox.application.use_cases.execute_command.ExecuteCommandUseCase`
            (or duck-typed equivalent).  When ``None``, the default
            :class:`~agent_sandbox.application.use_cases.execute_command.ExecuteCommandUseCase`
            is constructed automatically.  Inject a factory to override in tests.

    Example::

        uc = RunAgentUseCase(
            start_sandbox_use_case=start_uc,
            stop_sandbox_use_case=stop_uc,
        )
        result = uc.execute(
            config=config,
            image_spec=image_spec,
            containerfile_content=containerfile,
            agent_cmd=["claude", "--print", "hello"],
        )
        print(result.exit_code)
    """

    def __init__(
        self,
        start_sandbox_use_case: object,
        stop_sandbox_use_case: object,
        execute_command_use_case_factory: Callable | None = None,
    ) -> None:
        self._start = start_sandbox_use_case
        self._stop = stop_sandbox_use_case
        self._exec_factory = execute_command_use_case_factory

    def execute(
        self,
        config: SandboxConfig,
        image_spec: ImageSpec,
        containerfile_content: str,
        agent_cmd: list[str],
        timeout: float | None = None,
    ) -> ExecResult:
        """Run *agent_cmd* inside an isolated container and return the result.

        Flow:
          1. Start the container (includes image build-on-miss).
          2. Execute *agent_cmd* inside the running container.
          3. Stop and remove the container — **guaranteed** via ``try/finally``
             even when step 2 raises an exception or is interrupted.

        A non-zero exit code from *agent_cmd* is **not** an error — it is
        faithfully returned in :attr:`~agent_sandbox.domain.entities.ExecResult.exit_code`.
        :class:`~agent_sandbox.exceptions.TimeoutError` is raised (and the
        container is still cleaned up) when *timeout* is exceeded.

        Args:
            config: Validated sandbox configuration aggregate.
            image_spec: Domain value object carrying the image tag (cache key).
            containerfile_content: Full text of the Containerfile used when
                a cache miss requires a new build.
            agent_cmd: Command + argument list to execute inside the container
                (e.g. ``["claude", "--print", "Hello world"]``).
            timeout: Optional wall-clock timeout in seconds.  ``None`` means
                no explicit timeout.  When exceeded,
                :class:`~agent_sandbox.exceptions.TimeoutError` is raised with
                the container still cleaned up via the ``finally`` block.

        Returns:
            An :class:`~agent_sandbox.domain.entities.ExecResult` with
            ``exit_code``, ``stdout``, ``stderr``, ``duration_ms``, and
            ``timed_out`` populated.

        Raises:
            SandboxError: With code ``IMAGE_BUILD_FAILED`` if image build fails
                (raised before container start; no cleanup needed).
            SandboxError: With code ``CONTAINER_START_FAILED`` if the runtime
                fails to start the container (raised before exec; no cleanup
                needed as the adapter handles rollback).
            TimeoutError: With code ``EXEC_TIMEOUT`` if *timeout* is exceeded.
                The container is cleaned up in the ``finally`` block before
                propagating.
            KeyboardInterrupt: Propagated unchanged after cleanup — the caller
                (CLI) decides the exit code.
        """
        # Step 1: Start the container.
        # If this raises SandboxError, no container was created so no cleanup needed.
        handle = self._start.execute(config, image_spec, containerfile_content)

        # Step 2 (in try/finally): Execute the command, then ALWAYS stop.
        try:
            exec_uc = self._get_exec_use_case(handle)
            return exec_uc.execute(agent_cmd, timeout=timeout)
        finally:
            # Guaranteed cleanup: stop() even on exception, TimeoutError, or SIGINT.
            self._stop.execute(handle)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_exec_use_case(self, handle: object) -> object:
        """Return the execute use case for *handle*, using the factory if provided."""
        if self._exec_factory is not None:
            return self._exec_factory(handle)

        # Default: construct ExecuteCommandUseCase with the handle directly.
        # Late import so this module stays free of circular dependencies at
        # module-load time.
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        return ExecuteCommandUseCase(handle)
