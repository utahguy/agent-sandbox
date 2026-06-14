"""Sandbox application facade.

The ``Sandbox`` class is the primary public entry point for library consumers
(e.g. the Agent Execution Framework).  It composes default infrastructure
adapters and application use cases into a clean, synchronous Python API.

Usage::

    from agent_sandbox import Sandbox, SandboxConfig

    config = SandboxConfig.from_file(".agent-sandbox")
    sandbox = Sandbox(config=config)
    handle = sandbox.start()
    try:
        result = handle.exec(["ls", "-la", "/workspace"])
        print(result.stdout)
    finally:
        handle.stop()

    # Or with a context manager:
    with Sandbox(config) as handle:
        result = handle.exec(["echo", "hello"])

Architecture notes:
  - This module is the *composition root* for the library-consumer path.
  - It imports from domain, application, and infrastructure layers — all
    dependencies are directed inward.
  - Heavy frameworks (click, fastapi, sqlalchemy, etc.) are NOT imported here.
  - Use cases (StartSandboxUseCase, StopSandboxUseCase) encapsulate orchestration
    logic; this facade only wires adapters and delegates.
  - For testing, injectable ``_start_use_case`` / ``_stop_use_case`` kwargs
    allow pure unit tests with fake use cases, avoiding real Docker/Podman calls.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_sandbox.application.ports import ContainerHandlePort
from agent_sandbox.domain.entities import SandboxConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bundled Containerfile path (used to compute ImageSpec.tooling_fingerprint)
# ---------------------------------------------------------------------------

_CONTAINERFILE_PATH = Path(__file__).parent / "infrastructure" / "Containerfile"

# Base image used in the bundled Containerfile FROM directive
_BASE_IMAGE = "ubuntu:22.04"


def _load_containerfile() -> str:
    """Read the bundled Containerfile and return its contents."""
    return _CONTAINERFILE_PATH.read_text(encoding="utf-8")


def _compute_fingerprint(containerfile_content: str) -> str:
    """Compute a deterministic fingerprint for the given Containerfile content.

    Used as the ``tooling_fingerprint`` in :class:`~agent_sandbox.domain.image_spec.ImageSpec`.
    Changing the Containerfile changes the fingerprint, triggering a cache miss.
    """
    return hashlib.sha256(containerfile_content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Sandbox facade
# ---------------------------------------------------------------------------


class Sandbox:
    """Application facade for running AI agents in an isolated container.

    Composes default infrastructure adapters (runtime detection, image building,
    container lifecycle) and exposes a clean synchronous API.

    Args:
        config: Validated sandbox configuration aggregate.  Use
            :meth:`~agent_sandbox.domain.entities.SandboxConfig.from_file`
            to parse a ``.agent-sandbox`` file, or construct
            :class:`~agent_sandbox.domain.entities.SandboxConfig` directly
            for programmatic use.

    Keyword args (advanced / test injection):
        _start_use_case: Optional pre-built
            :class:`~agent_sandbox.application.use_cases.start_sandbox.StartSandboxUseCase`
            (or compatible duck-typed object).  When provided, the default
            adapter wiring is skipped.  Intended for unit tests.
        _stop_use_case: Optional pre-built
            :class:`~agent_sandbox.application.use_cases.stop_sandbox.StopSandboxUseCase`
            (or compatible duck-typed object).  Intended for unit tests.

    Examples::

        # Library consumer (AEF):
        config = SandboxConfig.from_file(".agent-sandbox")
        with Sandbox(config) as handle:
            result = handle.exec(["ls", "-la"])

        # Test injection:
        sandbox = Sandbox(config, _start_use_case=fake_start, _stop_use_case=fake_stop)
        handle = sandbox.start()
    """

    def __init__(
        self,
        config: SandboxConfig | None = None,
        *,
        _start_use_case: object | None = None,
        _stop_use_case: object | None = None,
    ) -> None:
        # Default to an empty config (all-defaults) if not provided.
        # This allows `Sandbox()` to work as a zero-argument convenience form.
        self._config = config if config is not None else SandboxConfig()
        self._start_use_case = _start_use_case
        self._stop_use_case = _stop_use_case
        self._handle: ContainerHandlePort | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> ContainerHandlePort:
        """Ensure the image is built, start an isolated container, and return a handle.

        On first call, wires the default infrastructure adapters
        (SubprocessRuntimeAdapter → ContainerfileImageBuilder +
        CliContainerAdapter) unless test-injected use cases are provided.

        Returns:
            A :class:`~agent_sandbox.application.ports.ContainerHandlePort`
            for the live container.  Call :meth:`exec` on it to run commands,
            and :meth:`stop` to tear it down.

        Raises:
            SandboxError: With code ``RUNTIME_NOT_FOUND`` if no container
                runtime is available.
            SandboxError: With code ``IMAGE_BUILD_FAILED`` if the image build
                fails.
            SandboxError: With code ``CONTAINER_START_FAILED`` if the
                container fails to start.
        """
        start_uc = self._get_start_use_case()
        stop_uc = self._get_stop_use_case()

        # Build image spec from the bundled Containerfile
        from agent_sandbox.domain.image_spec import ImageSpec

        containerfile_content = _load_containerfile()
        fingerprint = _compute_fingerprint(containerfile_content)
        image_spec = ImageSpec(
            base_image=_BASE_IMAGE,
            tooling_fingerprint=fingerprint,
        )

        logger.info("sandbox_starting config_path=%s", self._config.config_path)
        self._handle = start_uc.execute(self._config, image_spec, containerfile_content)
        # Store stop use case for cleanup
        self._stop_use_case = stop_uc
        logger.info(
            "sandbox_started container_id=%s", self._handle.container_id
        )
        return self._handle

    def stop(self) -> None:
        """Stop the running container (idempotent, safe to call without prior start).

        If :meth:`start` has not been called, or the container has already been
        stopped, this method is a no-op.

        Raises:
            Never raises — errors from the runtime are silenced (the container
            may already be gone).
        """
        if self._handle is None:
            return

        handle = self._handle
        self._handle = None

        stop_uc = self._get_stop_use_case()
        try:
            stop_uc.execute(handle)
        except Exception:
            # Best-effort cleanup: log and swallow
            logger.warning(
                "sandbox_stop_error container_id=%s", handle.container_id, exc_info=True
            )

    # ------------------------------------------------------------------
    # Context manager support (Criterion 5)
    # ------------------------------------------------------------------

    def __enter__(self) -> ContainerHandlePort:
        """Start the container and return the live handle.

        Returns:
            The :class:`~agent_sandbox.application.ports.ContainerHandlePort`
            for use inside the ``with`` block.
        """
        return self.start()

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> bool:
        """Stop the container on exit (even if an exception was raised).

        Args:
            exc_type: Type of exception raised in the body (``None`` if clean).
            exc_val: Exception instance (``None`` if clean).
            exc_tb: Traceback object (``None`` if clean).

        Returns:
            ``False`` — exceptions are NOT suppressed.
        """
        self.stop()
        return False  # never suppress exceptions

    # ------------------------------------------------------------------
    # Private helpers: lazy use-case wiring
    # ------------------------------------------------------------------

    def _get_start_use_case(self) -> object:
        """Return the start use case, creating default adapters if needed."""
        if self._start_use_case is not None:
            return self._start_use_case

        # Late imports for default adapter wiring — keeps this file framework-free
        # at module-level and allows injection in tests.
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.application.use_cases.start_sandbox import StartSandboxUseCase
        from agent_sandbox.domain.value_objects import RuntimeKind
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        # Detect runtime based on config preference
        preferred = self._config.runtime
        runtime_adapter = SubprocessRuntimeAdapter(preferred=preferred)
        runtime_adapter.detect()  # Probes for podman/docker on PATH

        image_builder = ContainerfileImageBuilder(runtime_port=runtime_adapter)
        container_adapter = CliContainerAdapter(runtime_port=runtime_adapter)
        ensure_image = EnsureImageUseCase(image_builder=image_builder)

        return StartSandboxUseCase(
            container_port=container_adapter,
            ensure_image_use_case=ensure_image,
        )

    def _get_stop_use_case(self) -> object:
        """Return the stop use case, creating default adapter if needed."""
        if self._stop_use_case is not None:
            return self._stop_use_case

        from agent_sandbox.application.use_cases.stop_sandbox import StopSandboxUseCase
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        preferred = self._config.runtime
        runtime_adapter = SubprocessRuntimeAdapter(preferred=preferred)
        runtime_adapter.detect()

        container_adapter = CliContainerAdapter(runtime_port=runtime_adapter)
        return StopSandboxUseCase(container_port=container_adapter)
