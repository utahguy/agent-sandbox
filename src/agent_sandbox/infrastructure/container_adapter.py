"""Infrastructure adapter: CliContainerAdapter + CliContainerHandle.

Implements :class:`~agent_sandbox.application.ports.ContainerPort` and
:class:`~agent_sandbox.application.ports.ContainerHandlePort` via docker/podman
CLI invocations (argument lists only — no shell strings).

Security properties (ADR-006, ADR-003):
  - All subprocess calls use argument lists (never ``shell=True``), preventing
    command injection from config values or agent arguments.
  - Containers are started with isolation/least-privilege flags:
      - ``-d`` (detached, non-interactive)
      - ``--rm`` (auto-remove on exit — no orphaned containers)
      - No ``--privileged`` flag
      - Rootless flags injected by ``SubprocessRuntimeAdapter`` for the
        ``run`` subcommand (``--userns=keep-id`` for Podman, ``--security-opt``
        for Docker)
  - Volume mounts, port mappings, and env vars are translated from typed domain
    objects to safe positional argument tokens, never interpolated into shell
    strings.
  - Secret env values are never logged.

Dependency rule: this module may import from domain, application/ports,
the Python standard library.  It must NOT import click, fastapi, sqlalchemy,
flask, django, or any other heavy framework.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from agent_sandbox.application.ports import ContainerHandlePort, ContainerPort, RuntimePort
from agent_sandbox.domain.entities import ExecResult, SandboxConfig
from agent_sandbox.exceptions import ErrorCode, SandboxError
from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CliContainerHandle — live container reference
# ---------------------------------------------------------------------------


class CliContainerHandle:
    """Live reference to a running container started by :class:`CliContainerAdapter`.

    Implements :class:`~agent_sandbox.application.ports.ContainerHandlePort`.

    Carries the container ID and a reference to the runtime port so it can
    issue ``exec`` and ``stop`` CLI calls.  A ``_stopped`` flag makes
    :meth:`stop` idempotent: the second call is always a no-op.

    Args:
        container_id: Runtime-assigned container identifier.
        image_tag: Tag of the image the container was launched from.
        runtime_port: The same :class:`~agent_sandbox.application.ports.RuntimePort`
            used to start the container; used for ``exec`` and ``stop`` CLI
            calls.
    """

    def __init__(
        self,
        container_id: str,
        image_tag: str,
        runtime_port: RuntimePort,
    ) -> None:
        self._container_id = container_id
        self._image_tag = image_tag
        self._runtime = runtime_port
        self._stopped = False

    # ------------------------------------------------------------------
    # ContainerHandlePort interface
    # ------------------------------------------------------------------

    @property
    def container_id(self) -> str:
        """Runtime-assigned container identifier."""
        return self._container_id

    @property
    def image_tag(self) -> str:
        """Tag of the image the container was launched from."""
        return self._image_tag

    def exec(
        self,
        cmd: str | list[str],
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute *cmd* inside the running container.

        A non-zero exit code is **not** an error — it is faithfully returned
        in :attr:`~agent_sandbox.domain.entities.ExecResult.exit_code`.

        Timeout behaviour:
            If *timeout* is exceeded, :class:`~agent_sandbox.exceptions.TimeoutError`
            is raised with code ``EXEC_TIMEOUT`` and ``timed_out=True``.
            The process is killed by the underlying runtime; no ``ExecResult``
            is returned in this case.

        Args:
            cmd: Command to run, either as a shell string or an argument list.
                Shell strings are split on whitespace.  Argument lists are
                forwarded verbatim — no shell interpolation occurs.
            timeout: Optional wall-clock timeout in seconds.  ``None`` means
                no explicit timeout.

        Returns:
            An :class:`~agent_sandbox.domain.entities.ExecResult` with
            ``exit_code``, ``stdout``, ``stderr``, ``duration_ms``, and
            ``timed_out=False``.

        Raises:
            TimeoutError: With code ``EXEC_TIMEOUT`` if *timeout* is exceeded.
        """
        # Normalise string commands to a list (split on whitespace).
        # Argument lists are forwarded as-is — no shell injection risk.
        if isinstance(cmd, str):
            cmd_list = cmd.split()
        else:
            cmd_list = list(cmd)

        args = ["exec", self._container_id] + cmd_list

        logger.debug(
            "exec_started container_id=%s cmd_hash=%s",
            self._container_id,
            hash(tuple(cmd_list)),
        )
        start_ns = time.monotonic_ns()
        try:
            exit_code, stdout, stderr = self._runtime.run_cli(args, timeout=timeout)
        except subprocess.TimeoutExpired:
            duration_ms = (time.monotonic_ns() - start_ns) // 1_000_000
            logger.warning(
                "exec_timeout container_id=%s duration_ms=%d timeout=%s",
                self._container_id,
                duration_ms,
                timeout,
            )
            raise SandboxTimeoutError(
                f"Command exceeded timeout of {timeout}s in container "
                f"'{self._container_id}': {cmd_list!r}",
                code=ErrorCode.EXEC_TIMEOUT,
            )

        duration_ms = (time.monotonic_ns() - start_ns) // 1_000_000

        logger.debug(
            "exec_finished container_id=%s exit_code=%d duration_ms=%d",
            self._container_id,
            exit_code,
            duration_ms,
        )
        return ExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=False,
        )

    def stop(self) -> None:
        """Stop and remove the container (idempotent).

        On the first call:
          1. Issues ``stop <container_id>`` to the runtime (errors ignored —
             the container may already be gone).
          2. Issues ``rm -f <container_id>`` to ensure removal (errors ignored —
             the ``--rm`` flag on ``run`` may have already removed it).

        On subsequent calls: returns immediately (no-op).

        Guarantees no orphaned container remains after this call returns.
        """
        if self._stopped:
            return

        self._stopped = True
        logger.debug("container_stopping container_id=%s", self._container_id)

        # Stop the container — ignore errors (may already be stopped/removed)
        self._runtime.run_cli(["stop", self._container_id])

        # Explicit rm -f for belt-and-suspenders cleanup in case --rm didn't fire
        self._runtime.run_cli(["rm", "-f", self._container_id])

        logger.info("container_stopped container_id=%s", self._container_id)


# ---------------------------------------------------------------------------
# CliContainerAdapter — ContainerPort implementation
# ---------------------------------------------------------------------------


def _build_volume_arg(volume) -> list[str]:
    """Convert a :class:`~agent_sandbox.domain.value_objects.Volume` to CLI tokens.

    Format: ``-v HOST:CONTAINER:MODE``
    """
    spec = f"{volume.host_path}:{volume.container_path}:{volume.mode}"
    return ["-v", spec]


def _build_port_arg(port) -> list[str]:
    """Convert a :class:`~agent_sandbox.domain.value_objects.PortMapping` to CLI tokens.

    Format: ``-p HOST_PORT:CONTAINER_PORT/PROTO``
    """
    spec = f"{port.host_port}:{port.container_port}/{port.protocol}"
    return ["-p", spec]


def _build_env_arg(key: str, value: str) -> list[str]:
    """Convert an env var key/value to CLI tokens.

    Format: ``-e KEY=VALUE``

    Note: values are not logged; only the key name is safe to emit.
    """
    return ["-e", f"{key}={value}"]


def _build_memory_arg(memory_limit) -> list[str]:
    """Convert a :class:`~agent_sandbox.domain.value_objects.MemoryLimit` to CLI tokens.

    Format: ``-m VALUEunit`` (e.g. ``-m 512m``)
    """
    return ["-m", f"{memory_limit.value}{memory_limit.unit}"]


def _build_claude_config_args(claude_config_dir: Path, tmpdir: str) -> list[str]:
    """Emit volume mounts for a custom Claude config directory.

    Mirrors the Bash CLI auth-mount logic for the ``claude-config:`` directive:

    - ``~/.credentials.json`` from the named dir is mounted read-write so token
      refreshes persist back to the host.
    - ``settings.json`` / ``settings.local.json`` are copied into *tmpdir* and
      mounted read-only so container writes cannot corrupt the originals.

    The container path for credentials and settings is always
    ``/home/claude/.claude/…`` because the container user's ``HOME`` is
    ``/home/claude`` and Claude Code defaults to ``~/.claude``.

    Args:
        claude_config_dir: Expanded host path to the Claude config directory.
        tmpdir: Caller-managed temporary directory for settings file copies.
            The caller must ensure this directory outlives the container start
            call (bind mounts hold inode references, so the host path may be
            removed immediately after ``podman/docker run`` returns).

    Returns:
        Flat list of CLI tokens to append to the ``run`` argument list.
        Returns an empty list if the directory does not exist (a warning is
        logged; the container starts without those mounts).
    """
    if not claude_config_dir.is_dir():
        logger.warning(
            "claude_config_dir_missing path=%s skipping_credential_mounts",
            claude_config_dir,
        )
        return []

    args: list[str] = []

    # Credentials — read-write so token refreshes persist to the host
    creds = claude_config_dir / ".credentials.json"
    if creds.is_file():
        args.extend(["-v", f"{creds}:/home/claude/.claude/.credentials.json:Z"])

    # Settings — copy to tmpdir and mount read-only
    for src_name, container_path in (
        ("settings.json", "/tmp/claude-settings-src"),
        ("settings.local.json", "/tmp/claude-settings-local-src"),
    ):
        src = claude_config_dir / src_name
        if src.is_file():
            dst = Path(tmpdir) / src_name
            shutil.copy2(src, dst)
            dst.chmod(0o644)
            args.extend(["-v", f"{dst}:{container_path}:ro,Z"])

    return args


class CliContainerAdapter:
    """Container lifecycle adapter wrapping ``docker``/``podman`` CLI calls.

    Implements :class:`~agent_sandbox.application.ports.ContainerPort`.

    All subprocess calls are delegated to the injected
    :class:`~agent_sandbox.application.ports.RuntimePort`, ensuring:

    1. No direct subprocess knowledge in this class.
    2. Tests can inject a fake runtime without real Docker/Podman.
    3. All CLI calls use argument lists — no shell injection risk.

    Isolation guarantees (per ADR-006):
        - ``--rm`` flag: auto-remove on container exit → no orphaned containers.
        - ``-d`` flag: detached mode; process is managed by the runtime.
        - No ``--privileged`` flag: least-privilege policy.
        - Rootless security flags are injected by the RuntimePort implementation
          for the ``run`` subcommand (``--userns=keep-id`` for Podman,
          ``--security-opt=no-new-privileges`` for Docker).

    Args:
        runtime_port: An implementation of
            :class:`~agent_sandbox.application.ports.RuntimePort` (typically
            :class:`~agent_sandbox.infrastructure.subprocess_runtime.SubprocessRuntimeAdapter`).
            Must have already had :meth:`~RuntimePort.detect` called so the
            resolved binary is known.
    """

    def __init__(self, runtime_port: RuntimePort) -> None:
        self._runtime = runtime_port

    # ------------------------------------------------------------------
    # ContainerPort interface
    # ------------------------------------------------------------------

    def start(
        self,
        config: SandboxConfig,
        image_tag: str,
    ) -> CliContainerHandle:
        """Start a new isolated container and return a live handle.

        Builds a ``run -d --rm [flags] image_tag sleep infinity`` argument list
        from the validated config, invokes the runtime, and returns a
        :class:`CliContainerHandle` wrapping the assigned container ID.

        Rollback:
            If the runtime returns a non-zero exit code but stdout contains a
            partial container ID (e.g. the container was created but failed to
            start), this method issues ``rm -f <partial_id>`` before raising
            to guarantee no orphaned container remains.

        Args:
            config: Validated sandbox configuration aggregate.
            image_tag: Tag of the pre-built image to launch.

        Returns:
            A :class:`CliContainerHandle` for the live container.

        Raises:
            SandboxError: With code ``CONTAINER_START_FAILED`` if the runtime
                returns a non-zero exit code.
        """
        # Create a temp dir for settings file copies when claude_config_dir is set.
        # The tmpdir is cleaned up in the finally block after podman/docker run
        # returns; bind mounts hold inode references so deletion is safe immediately
        # after the container starts.
        tmpdir = (
            tempfile.mkdtemp(prefix="agent-sandbox-claude-cfg-")
            if config.claude_config_dir is not None
            else None
        )
        try:
            args = self._build_run_args(config, image_tag, claude_tmpdir=tmpdir)
        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)

        logger.info(
            "container_start_requested image_tag=%s volumes=%d ports=%d env_keys=%d",
            image_tag,
            len(config.volumes),
            len(config.ports),
            len(config.env),
        )

        exit_code, stdout, stderr = self._runtime.run_cli(args)
        container_id = stdout.strip()

        if exit_code != 0:
            # Rollback: if a partial container_id was emitted, remove it.
            if container_id:
                logger.warning(
                    "container_start_rollback container_id=%s", container_id
                )
                self._runtime.run_cli(["rm", "-f", container_id])

            detail = stderr.strip() or stdout.strip() or "non-zero exit code"
            logger.error(
                "container_start_failed image_tag=%s exit_code=%d detail=%r",
                image_tag,
                exit_code,
                detail,
            )
            raise SandboxError(
                f"Failed to start container from image '{image_tag}' "
                f"(exit {exit_code}): {detail}",
                code=ErrorCode.CONTAINER_START_FAILED,
            )

        logger.info(
            "container_started container_id=%s image_tag=%s",
            container_id,
            image_tag,
        )
        return CliContainerHandle(
            container_id=container_id,
            image_tag=image_tag,
            runtime_port=self._runtime,
        )

    def exec(
        self,
        handle: ContainerHandlePort,
        cmd: str | list[str],
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute *cmd* in the container identified by *handle*.

        Delegates to :meth:`ContainerHandlePort.exec`.

        Args:
            handle: The live container reference returned by :meth:`start`.
            cmd: Command to run inside the container.
            timeout: Optional wall-clock timeout in seconds.

        Returns:
            An :class:`~agent_sandbox.domain.entities.ExecResult`.
        """
        return handle.exec(cmd, timeout=timeout)

    def stop(self, handle: ContainerHandlePort) -> None:
        """Stop and remove the container identified by *handle* (idempotent).

        Delegates to :meth:`ContainerHandlePort.stop`.

        Args:
            handle: The live container reference to stop.
        """
        handle.stop()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_run_args(
        config: SandboxConfig,
        image_tag: str,
        claude_tmpdir: str | None = None,
    ) -> list[str]:
        """Build the ``run`` argument list from *config* and *image_tag*.

        Security properties:
          - ``-d``: detached (non-interactive, daemon)
          - ``--rm``: auto-remove on container exit (no orphans)
          - No ``--privileged`` flag (least privilege)
          - Volumes, ports, env, memory from typed domain objects (no string
            interpolation into the list itself)
          - The command to keep the container alive is ``sleep infinity``

        Args:
            config: Validated sandbox configuration aggregate.
            image_tag: Tag of the image to run.
            claude_tmpdir: Caller-managed temp directory used by
                :func:`_build_claude_config_args` for settings file copies.
                Must be set when ``config.claude_config_dir`` is not ``None``.

        Returns:
            Argument list starting with ``"run"`` (the binary is prepended by
            :class:`~agent_sandbox.infrastructure.subprocess_runtime.SubprocessRuntimeAdapter`
            via ``run_cli``).
        """
        args: list[str] = [
            "run",
            "-d",        # detached
            "--rm",      # auto-remove on exit — no orphaned containers
            # Note: rootless flags (--userns=keep-id, --security-opt) are
            # injected by SubprocessRuntimeAdapter._build_args for "run"
        ]

        # Bind-mount volumes
        for vol in config.volumes:
            args.extend(_build_volume_arg(vol))

        # Port mappings
        for port in config.ports:
            args.extend(_build_port_arg(port))

        # Environment variables (values not logged for security)
        for key, value in config.env.items():
            args.extend(_build_env_arg(key, value))

        # Project-declared apt packages — passed as a space-separated env var
        # so the root phase of the entrypoint can install them before dropping
        # privileges to the project user.
        if config.packages:
            args.extend(_build_env_arg("SANDBOX_APT_PACKAGES", " ".join(config.packages)))

        # Memory limit
        if config.memory_limit is not None:
            args.extend(_build_memory_arg(config.memory_limit))

        # Claude account credentials from a named config directory
        if config.claude_config_dir is not None and claude_tmpdir is not None:
            args.extend(_build_claude_config_args(config.claude_config_dir, claude_tmpdir))

        # Image tag
        args.append(image_tag)

        # Keep-alive command: sleep infinity keeps the container running so
        # exec() calls can be made after start() returns.
        args.extend(["sleep", "infinity"])

        return args
