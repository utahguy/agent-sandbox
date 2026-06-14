"""Infrastructure adapter: SubprocessRuntimeAdapter.

Implements :class:`~agent_sandbox.application.ports.RuntimePort` using
argument-list subprocess invocation — never a shell string — to detect and
invoke the container runtime CLI.

Security properties (ADR-003):
  - All subprocess calls use ``args: list[str]`` (never ``shell=True``), which
    prevents command injection from config values or agent arguments.
  - AUTO mode prefers Podman (rootless by default) over Docker.
  - ``run`` invocations include rootless / least-privilege flags:
      - Podman: ``--userns=keep-id`` (UID/GID mapping for rootless mounts)
      - Docker: ``--security-opt=no-new-privileges`` (process-level hardening)

Dependency rule: this module may import from domain, application/ports,
the Python standard library, and ``subprocess``.  It must NOT import
click, fastapi, sqlalchemy, flask, django, docker SDK, or any other
heavy framework.
"""

from __future__ import annotations

import subprocess
from typing import Callable

from agent_sandbox.domain.value_objects import RuntimeKind
from agent_sandbox.exceptions import ErrorCode, SandboxError

# ---------------------------------------------------------------------------
# Default subprocess runner
# ---------------------------------------------------------------------------

def _default_runner(
    args: list[str],
    timeout: float | None = None,
) -> tuple[int, str, str]:
    """Run *args* as a subprocess without a shell and return (exit_code, stdout, stderr).

    The timeout applies to the wall-clock duration of the subprocess.  If no
    timeout is given, the call blocks until the process exits.

    Args:
        args: Argument list to pass to :func:`subprocess.run`.
        timeout: Optional wall-clock timeout in seconds.

    Returns:
        ``(exit_code, stdout, stderr)`` where both output streams are decoded
        as UTF-8 with errors replaced.
    """
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Rootless flags per runtime (injected into "run" subcommand invocations)
# ---------------------------------------------------------------------------

_ROOTLESS_FLAGS: dict[RuntimeKind, list[str]] = {
    # Podman is rootless by default; --userns=keep-id maps the host UID/GID
    # so files written inside the container are owned by the invoking user.
    RuntimeKind.PODMAN: ["--userns=keep-id"],
    # Docker: --security-opt=no-new-privileges prevents privilege escalation
    # via setuid binaries inside the container.
    RuntimeKind.DOCKER: ["--security-opt=no-new-privileges"],
}

# Binary name for each concrete runtime kind
_BINARY: dict[RuntimeKind, str] = {
    RuntimeKind.PODMAN: "podman",
    RuntimeKind.DOCKER: "docker",
}

# Human-readable install hints per runtime kind (used in error messages)
_INSTALL_HINT: dict[RuntimeKind, str] = {
    RuntimeKind.PODMAN: "https://podman.io/get-started",
    RuntimeKind.DOCKER: "https://docs.docker.com/get-docker/",
}


class SubprocessRuntimeAdapter:
    """Container runtime adapter that wraps ``docker`` / ``podman`` subprocesses.

    Implements :class:`~agent_sandbox.application.ports.RuntimePort`.

    All subprocess calls use argument lists — the injected *runner* callable
    (or :func:`_default_runner`) is always called with ``list[str]``, never
    a shell string, preventing command injection.

    Args:
        preferred: Runtime preference — ``AUTO`` (Podman > Docker), ``DOCKER``,
            or ``PODMAN``.  Defaults to ``AUTO``.
        runner: Optional injectable callable with signature
            ``(args: list[str], timeout: float | None) -> (int, str, str)``.
            Defaults to :func:`_default_runner`.  Pass a fake in tests to avoid
            real subprocess calls.
    """

    def __init__(
        self,
        preferred: RuntimeKind = RuntimeKind.AUTO,
        runner: Callable[..., tuple[int, str, str]] | None = None,
    ) -> None:
        self._preferred = preferred
        self._runner: Callable[..., tuple[int, str, str]] = runner or _default_runner
        # Resolved runtime after detect() is called; None until then.
        self._resolved: RuntimeKind | None = None

    # ------------------------------------------------------------------
    # RuntimePort interface
    # ------------------------------------------------------------------

    def detect(self) -> RuntimeKind:
        """Detect which container runtime is available based on *preferred*.

        - ``AUTO``: probes Podman first (rootless-preferred), then Docker.
        - ``PODMAN``: validates Podman is on PATH; raises if absent.
        - ``DOCKER``: validates Docker is on PATH; raises if absent.

        Sets ``self._resolved`` so that :meth:`run_cli` knows which binary to
        prepend to the argument list.

        Returns:
            The resolved :class:`~agent_sandbox.domain.value_objects.RuntimeKind`
            (``PODMAN`` or ``DOCKER``).

        Raises:
            SandboxError: With code ``RUNTIME_NOT_FOUND`` if the requested
                runtime is not found.  The message includes actionable install
                guidance.
        """
        if self._preferred == RuntimeKind.AUTO:
            return self._detect_auto()
        elif self._preferred == RuntimeKind.PODMAN:
            return self._detect_explicit(RuntimeKind.PODMAN)
        else:
            return self._detect_explicit(RuntimeKind.DOCKER)

    def run_cli(
        self,
        args: list[str],
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        """Run the container runtime CLI with *args* as an argument list.

        Prepends the binary name (``docker`` or ``podman``) and, for ``run``
        subcommands, injects rootless / security flags after the subcommand
        name.

        :meth:`detect` must be called before :meth:`run_cli` so the adapter
        knows which binary to use.

        Args:
            args: Argument list passed to the runtime binary (e.g.
                ``["run", "--rm", "ubuntu:22.04", "echo", "hello"]``).
            timeout: Optional wall-clock timeout in seconds.

        Returns:
            ``(exit_code, stdout, stderr)`` from the subprocess.

        Raises:
            SandboxError: If :meth:`detect` has not been called yet.
        """
        if self._resolved is None:
            raise SandboxError(
                "SubprocessRuntimeAdapter.detect() must be called before run_cli(). "
                "Call detect() first to identify the available runtime.",
                code=ErrorCode.RUNTIME_NOT_FOUND,
            )
        binary = _BINARY[self._resolved]
        full_args = self._build_args(binary, args)
        return self._runner(full_args, timeout=timeout)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _detect_auto(self) -> RuntimeKind:
        """Auto-mode: try Podman first (rootless preferred), then Docker."""
        if self._probe("podman"):
            self._resolved = RuntimeKind.PODMAN
            return RuntimeKind.PODMAN
        if self._probe("docker"):
            self._resolved = RuntimeKind.DOCKER
            return RuntimeKind.DOCKER
        raise SandboxError(
            "No supported container runtime found. "
            "Install Docker ("
            + _INSTALL_HINT[RuntimeKind.DOCKER]
            + ") or Podman ("
            + _INSTALL_HINT[RuntimeKind.PODMAN]
            + ") and ensure the binary is on your PATH.",
            code=ErrorCode.RUNTIME_NOT_FOUND,
        )

    def _detect_explicit(self, kind: RuntimeKind) -> RuntimeKind:
        """Explicit-mode: validate the requested runtime is available."""
        binary = _BINARY[kind]
        if self._probe(binary):
            self._resolved = kind
            return kind
        raise SandboxError(
            f"{binary.capitalize()} not found on PATH. "
            f"Install it from {_INSTALL_HINT[kind]} "
            f"or switch to 'runtime auto' to let the system pick an available runtime.",
            code=ErrorCode.RUNTIME_NOT_FOUND,
        )

    def _probe(self, binary: str) -> bool:
        """Return True if *binary* is on PATH and responds to --version.

        Uses the injected runner so tests can mock subprocess calls.
        FileNotFoundError (binary absent) is caught and returns False.
        Non-zero exit codes are treated as "available but unhealthy" and
        still return True — the caller only needs to know the binary exists.

        Args:
            binary: Executable name, e.g. ``"podman"`` or ``"docker"``.

        Returns:
            ``True`` if the binary is found and runnable, ``False`` otherwise.
        """
        try:
            exit_code, _, _ = self._runner([binary, "--version"])
            return exit_code == 0
        except (FileNotFoundError, OSError):
            return False

    def _build_args(self, binary: str, args: list[str]) -> list[str]:
        """Build the full argument list: ``[binary, *args]`` with rootless flags.

        For ``run`` subcommands, rootless / security flags are inserted
        immediately after ``"run"`` so they apply to the container being
        started rather than to a later positional argument.

        Args:
            binary: Runtime binary name (``"docker"`` or ``"podman"``).
            args: Caller-supplied argument list (first element is the
                subcommand, e.g. ``"run"``, ``"ps"``, ``"inspect"``).

        Returns:
            Complete argument list ready to pass to the runner.
        """
        if not args:
            return [binary]

        subcommand = args[0]
        if subcommand == "run" and self._resolved in _ROOTLESS_FLAGS:
            rootless_flags = _ROOTLESS_FLAGS[self._resolved]
            # [binary, "run", *rootless_flags, *remaining_args]
            return [binary, subcommand] + rootless_flags + list(args[1:])

        return [binary] + list(args)
