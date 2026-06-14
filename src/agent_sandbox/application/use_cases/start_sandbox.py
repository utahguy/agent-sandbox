"""Application use case: StartSandboxUseCase.

Implements US-005 (FR-003, ADR-006).

Flow:
  1. Ensure the container image exists (cache hit or build) via the injected
     ``ensure_image_use_case``.
  2. Start an isolated container via ``ContainerPort.start(config, image_tag)``,
     applying volumes/ports/env/memory from the config.
  3. Return the live ``ContainerHandlePort`` to the caller.
  4. On start failure, any partially-created container cleanup is the
     responsibility of the ``ContainerPort`` implementation; the use case
     propagates the ``SandboxError(CONTAINER_START_FAILED)`` unchanged.

Dependency rule: this module imports ONLY from ``agent_sandbox.domain``,
``agent_sandbox.application.ports``, and the Python standard library.
No subprocess, click, docker, podman, sqlalchemy, fastapi, or any
infrastructure import is permitted here.
"""

from __future__ import annotations

from agent_sandbox.application.ports import ContainerHandlePort, ContainerPort
from agent_sandbox.domain.entities import SandboxConfig
from agent_sandbox.domain.image_spec import ImageSpec


class StartSandboxUseCase:
    """Ensure the container image then start an isolated container.

    Orchestrates two subordinate operations:

    1. **Image ensure** — delegates to the injected ``ensure_image_use_case``
       (an :class:`~agent_sandbox.application.use_cases.ensure_image.EnsureImageUseCase`)
       which checks the local cache and builds if absent.

    2. **Container start** — delegates to the injected
       :class:`~agent_sandbox.application.ports.ContainerPort`, passing the
       validated config to apply volumes, ports, env, and memory limits.

    Cleanup responsibility:
        If the container port raises :class:`~agent_sandbox.exceptions.SandboxError`
        during ``start()``, any partially-created container must be cleaned up by
        the ``ContainerPort`` implementation (the infrastructure adapter).  The use
        case simply re-raises the error.

    Args:
        container_port: An implementation of
            :class:`~agent_sandbox.application.ports.ContainerPort` (typically
            :class:`~agent_sandbox.infrastructure.container_adapter.CliContainerAdapter`).
        ensure_image_use_case: An instance of
            :class:`~agent_sandbox.application.use_cases.ensure_image.EnsureImageUseCase`
            (duck-typed, not imported here to avoid circular layering).
    """

    def __init__(
        self,
        container_port: ContainerPort,
        ensure_image_use_case: object,
    ) -> None:
        self._container = container_port
        self._ensure_image = ensure_image_use_case

    def execute(
        self,
        config: SandboxConfig,
        image_spec: ImageSpec,
        containerfile_content: str,
    ) -> ContainerHandlePort:
        """Ensure the image is built, then start an isolated container.

        Args:
            config: Validated sandbox configuration aggregate.  Applied by
                the ``ContainerPort`` to volumes, ports, env, and memory limits.
            image_spec: Domain value object carrying the image tag (cache key)
                derived deterministically from ``base_image`` and
                ``tooling_fingerprint``.
            containerfile_content: Full text of the Containerfile used when
                a cache miss requires a new build.

        Returns:
            A :class:`~agent_sandbox.application.ports.ContainerHandlePort`
            representing the live, running container.

        Raises:
            SandboxError: With code ``IMAGE_BUILD_FAILED`` if the image build
                fails (propagated from ``ensure_image_use_case``).
            SandboxError: With code ``CONTAINER_START_FAILED`` if the runtime
                fails to start the container (propagated from
                ``container_port.start()``).
        """
        # Step 1: Ensure the image is available in the local cache.
        # Raises SandboxError(IMAGE_BUILD_FAILED) on failure.
        self._ensure_image.execute(image_spec, containerfile_content)

        # Step 2: Start the container.
        # ContainerPort implementation is responsible for cleanup of any
        # partially-created container if start() fails.
        # Raises SandboxError(CONTAINER_START_FAILED) on failure.
        handle = self._container.start(config, image_spec.tag)
        return handle
