"""Application use case: EnsureImageUseCase.

Orchestrates the check-or-build logic for the agent-sandbox container image.
Implements US-004 (FR-004, ADR-004).

Flow:
  1. Check whether an image tagged with :attr:`ImageSpec.tag` already exists
     in the local container cache via :meth:`ImageBuilderPort.is_cached`.
  2. If the image is present (cache hit), return immediately — no rebuild.
  3. If the image is absent (cache miss), delegate to
     :meth:`ImageBuilderPort.ensure_image` to build and cache it.

Dependency rule: this module imports ONLY from ``agent_sandbox.domain``,
``agent_sandbox.application.ports``, and the Python standard library.
No subprocess, click, docker, podman, sqlalchemy, fastapi, or any
infrastructure import is permitted here.
"""

from __future__ import annotations

from agent_sandbox.application.ports import ImageBuilderPort
from agent_sandbox.domain.image_spec import ImageSpec


class EnsureImageUseCase:
    """Check the local image cache and build if the image is absent.

    The use case itself holds no build or subprocess knowledge — all I/O is
    delegated to the injected :class:`~agent_sandbox.application.ports.ImageBuilderPort`.

    Cache identity is determined entirely by :attr:`~agent_sandbox.domain.image_spec.ImageSpec.tag`,
    which is a deterministic function of ``base_image`` and
    ``tooling_fingerprint``.  This guarantees that:

    - Identical inputs → cache hit → no redundant rebuild
    - Any change to the Containerfile or pinned tool version → cache miss →
      automatic rebuild

    Args:
        image_builder: An implementation of
            :class:`~agent_sandbox.application.ports.ImageBuilderPort`
            (typically
            :class:`~agent_sandbox.infrastructure.image_builder.ContainerfileImageBuilder`).

    Raises:
        SandboxError: With code ``IMAGE_BUILD_FAILED`` if the image build
            fails.  Raised by the port implementation and propagated here
            without wrapping (the port already attaches the machine code).
    """

    def __init__(self, image_builder: ImageBuilderPort) -> None:
        self._builder = image_builder

    def execute(self, image_spec: ImageSpec, containerfile_content: str) -> None:
        """Ensure the image identified by *image_spec* is available locally.

        Checks the local cache first.  If the image tag is present, this is a
        no-op.  If absent, builds the image from *containerfile_content* and
        caches it.

        Args:
            image_spec: Domain value object carrying the image tag (cache key)
                derived deterministically from ``base_image`` and
                ``tooling_fingerprint``.
            containerfile_content: Full text of the Containerfile to use when
                building the image on a cache miss.

        Returns:
            ``None`` — whether via cache hit or successful build.

        Raises:
            SandboxError: With code ``IMAGE_BUILD_FAILED`` if the build fails.
                The error is raised by the :class:`ImageBuilderPort`
                implementation and propagated to the caller unchanged.
        """
        if self._builder.is_cached(image_spec.tag):
            # Cache hit — image already exists locally; skip build entirely.
            return

        # Cache miss — build and cache the image.
        self._builder.ensure_image(image_spec.tag, containerfile_content)
