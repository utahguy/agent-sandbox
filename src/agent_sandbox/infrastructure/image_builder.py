"""Infrastructure adapter: ContainerfileImageBuilder.

Implements :class:`~agent_sandbox.application.ports.ImageBuilderPort` by
delegating to the container runtime CLI via
:class:`~agent_sandbox.application.ports.RuntimePort`.

Security properties:
  - All runtime CLI calls use argument lists (never shell strings), preventing
    command injection from image tags or containerfile paths.
  - Containerfile content is written to a temporary file and passed with ``-f``
    rather than piped through stdin, so the file path is a safe positional
    argument rather than interpolated into a shell command.
  - Temporary files are always cleaned up in a ``finally`` block.

Dependency rule: this module may import from domain, application/ports,
the Python standard library (``tempfile``, ``os``), and ``subprocess``
(indirectly via the injected RuntimePort).  It must NOT import
click, fastapi, sqlalchemy, flask, django, or any other heavy framework.
"""

from __future__ import annotations

import logging
import os
import tempfile

from agent_sandbox.application.ports import ImageBuilderPort, RuntimePort
from agent_sandbox.exceptions import ErrorCode, SandboxError

logger = logging.getLogger(__name__)


class ContainerfileImageBuilder:
    """Build and cache container images via the container runtime CLI.

    Implements :class:`~agent_sandbox.application.ports.ImageBuilderPort`.

    All subprocess calls are delegated to the injected
    :class:`~agent_sandbox.application.ports.RuntimePort`, ensuring that:

    1. This class holds no direct subprocess knowledge.
    2. Tests can inject a fake/mock runtime without real Docker/Podman.
    3. All CLI calls are argument-list based (no shell injection risk).

    Args:
        runtime_port: An implementation of
            :class:`~agent_sandbox.application.ports.RuntimePort` (typically
            :class:`~agent_sandbox.infrastructure.subprocess_runtime.SubprocessRuntimeAdapter`).
            The port's :meth:`~RuntimePort.run_cli` is called with argument
            lists to inspect and build images.
    """

    def __init__(self, runtime_port: RuntimePort) -> None:
        self._runtime = runtime_port

    # ------------------------------------------------------------------
    # ImageBuilderPort interface
    # ------------------------------------------------------------------

    def is_cached(self, image_tag: str) -> bool:
        """Check whether the image is already in the local cache.

        Calls the runtime CLI with ``["inspect", image_tag]`` (a no-op read
        that exits 0 if the image exists, non-zero otherwise).

        Args:
            image_tag: Fully-qualified image tag, e.g.
                ``"agent-sandbox:a3f9b2c1d4e5f6a7"``.

        Returns:
            ``True`` if the image exists locally, ``False`` otherwise.
        """
        exit_code, _, _ = self._runtime.run_cli(["inspect", image_tag])
        cached = exit_code == 0
        if cached:
            logger.debug("image_cache_hit tag=%s", image_tag)
        else:
            logger.debug("image_cache_miss tag=%s", image_tag)
        return cached

    def ensure_image(self, image_tag: str, containerfile_content: str) -> None:
        """Build the image from *containerfile_content* and tag it *image_tag*.

        The Containerfile content is written to a temporary file so it can be
        passed to the build command via the ``-f`` flag as a safe positional
        argument.  The temporary file is always removed after the build
        completes or fails.

        Args:
            image_tag: Target tag for the built image, e.g.
                ``"agent-sandbox:a3f9b2c1d4e5f6a7"``.
            containerfile_content: Full text of the Containerfile.

        Raises:
            SandboxError: With code ``IMAGE_BUILD_FAILED`` if the runtime
                returns a non-zero exit code during the build.  The error
                message includes the captured stderr/stdout for diagnosis.
        """
        logger.info("image_build_started tag=%s", image_tag)

        # Write Containerfile to a temporary file so we can pass it via -f
        # as a safe positional argument (not interpolated into a shell string).
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix="agent-sandbox-Containerfile-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                f.write(containerfile_content)

            # Build with argument list — no shell=True, no string interpolation.
            # Context directory is "." (current working directory, intentionally
            # minimal — callers that need a richer context can extend this adapter).
            exit_code, stdout, stderr = self._runtime.run_cli(
                [
                    "build",
                    "--file", tmp_path,
                    "--tag", image_tag,
                    ".",
                ]
            )

            if exit_code != 0:
                detail = (stderr.strip() or stdout.strip() or "non-zero exit code")
                logger.error(
                    "image_build_failed tag=%s exit_code=%d detail=%r",
                    image_tag, exit_code, detail,
                )
                raise SandboxError(
                    f"Image build failed for tag '{image_tag}' (exit {exit_code}): {detail}",
                    code=ErrorCode.IMAGE_BUILD_FAILED,
                )

            logger.info("image_build_succeeded tag=%s", image_tag)

        finally:
            # Always remove the temporary Containerfile — no secrets leaked to disk.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass  # Already removed or never created — safe to ignore.
