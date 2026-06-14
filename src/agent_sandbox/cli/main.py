"""Canonical CLI entry point for agent-sandbox — composition root (US-007, FR-016).

This module is the PRESENTATION layer composition root.  It imports from domain,
application, and infrastructure layers.  It must NOT contain business logic; it
only wires adapters into use cases and delegates.

Console entry point: ``agent-sandbox``

Usage::

    agent-sandbox --agent claude [agent_args...]

Behaviour:
  1. Load ``.agent-sandbox`` from CWD (empty config if not present).
  2. Build infrastructure adapters (runtime detection, image builder,
     container adapter) and compose them into application use cases.
  3. Run the agent command inside an isolated container via
     :class:`~agent_sandbox.application.use_cases.run_agent.RunAgentUseCase`.
  4. Print stdout / stderr from the container exec.
  5. Exit with the inner command's exit code on success.
  6. Exit with :data:`EXIT_SANDBOX_ERROR` (2) if a SandboxError occurs.
  7. Exit with :data:`EXIT_TIMEOUT` (124) if a TimeoutError occurs.
  8. Container cleanup is guaranteed via ``try/finally`` inside
     :class:`~agent_sandbox.application.use_cases.run_agent.RunAgentUseCase`.

Architecture note:
  This module is the sole place where infrastructure adapters are wired to
  application use cases.  All business logic lives in the application/domain
  layers.  Use :func:`_build_run_agent_use_case` as the seam for testing —
  monkeypatch it to inject fakes without touching Click internals.

Canonical entry: ``agent_sandbox.cli.main:main``
  Referenced by ``pyproject.toml [project.scripts]`` and the ``Containerfile``
  ENTRYPOINT, ensuring a single, consistent, importable composition root.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

import click

from agent_sandbox.exceptions import SandboxError
from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

#: Exit code for config / runtime / image-build errors (SandboxError).
EXIT_SANDBOX_ERROR: int = 2

#: Exit code for timeout (TimeoutError) — mirrors the Unix ``timeout`` command.
EXIT_TIMEOUT: int = 124

#: Exit code for SIGINT / KeyboardInterrupt.
EXIT_SIGINT: int = 130

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Containerfile helpers
# ---------------------------------------------------------------------------

_CONTAINERFILE_PATH = Path(__file__).parent.parent / "infrastructure" / "Containerfile"
_BASE_IMAGE = "ubuntu:22.04"


def _load_containerfile() -> str:
    """Read the bundled Containerfile and return its contents."""
    return _CONTAINERFILE_PATH.read_text(encoding="utf-8")


def _compute_fingerprint(containerfile_content: str) -> str:
    """Compute a deterministic fingerprint for the Containerfile content."""
    return hashlib.sha256(containerfile_content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Composition root — wires adapters into use cases
# ---------------------------------------------------------------------------


def _build_run_agent_use_case(config: object) -> object:
    """Build a :class:`~agent_sandbox.application.use_cases.run_agent.RunAgentUseCase`.

    This function is the infrastructure composition root — it wires the real
    infrastructure adapters (SubprocessRuntimeAdapter, ContainerfileImageBuilder,
    CliContainerAdapter) into the application use cases.

    Kept as a module-level function so tests can monkeypatch it to inject fakes
    without touching Click internals::

        monkeypatch.setattr("agent_sandbox.cli.main._build_run_agent_use_case", lambda c: fake_uc)

    Args:
        config: Validated :class:`~agent_sandbox.domain.entities.SandboxConfig`.

    Returns:
        A configured :class:`~agent_sandbox.application.use_cases.run_agent.RunAgentUseCase`.

    Raises:
        SandboxError: With code ``RUNTIME_NOT_FOUND`` if no container runtime
            is available on the host.
    """
    from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
    from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase
    from agent_sandbox.application.use_cases.start_sandbox import StartSandboxUseCase
    from agent_sandbox.application.use_cases.stop_sandbox import StopSandboxUseCase
    from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter
    from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder
    from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

    # Detect runtime based on config preference
    runtime_adapter = SubprocessRuntimeAdapter(preferred=config.runtime)
    runtime_adapter.detect()  # Raises SandboxError(RUNTIME_NOT_FOUND) if unavailable

    image_builder = ContainerfileImageBuilder(runtime_port=runtime_adapter)
    container_adapter = CliContainerAdapter(runtime_port=runtime_adapter)
    ensure_image_uc = EnsureImageUseCase(image_builder=image_builder)

    start_uc = StartSandboxUseCase(
        container_port=container_adapter,
        ensure_image_use_case=ensure_image_uc,
    )
    stop_uc = StopSandboxUseCase(container_port=container_adapter)

    return RunAgentUseCase(
        start_sandbox_use_case=start_uc,
        stop_sandbox_use_case=stop_uc,
        # execute_command_use_case_factory=None uses the default ExecuteCommandUseCase
    )


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@click.command(
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
    }
)
@click.option(
    "--agent",
    required=True,
    help="Agent to run inside the sandbox (e.g. 'claude').",
)
@click.argument("agent_args", nargs=-1, type=click.UNPROCESSED)
def main(agent: str, agent_args: tuple[str, ...]) -> None:
    """Run an AI agent in an isolated, reproducible sandbox container.

    Loads ``.agent-sandbox`` from the current directory, builds/caches the
    sandbox container image, starts an isolated container, runs AGENT with any
    additional AGENT_ARGS inside it, streams the output, and guarantees cleanup
    on exit — including exceptions and SIGINT.

    Exit codes:
      0        Inner command succeeded (or exited with 0).
      <N>      Inner command exited with non-zero N.
      2        SandboxError (config / runtime / image-build failure).
      124      Timeout (TimeoutError).
      130      Interrupted (SIGINT / Ctrl-C).

    Examples::

        agent-sandbox --agent claude
        agent-sandbox --agent claude --print "Hello from sandbox"
        agent-sandbox --agent claude -p "Write a hello-world script"
    """
    # ── Step 1: Load configuration ──────────────────────────────────────────
    config_path = Path.cwd() / ".agent-sandbox"

    try:
        if config_path.exists():
            from agent_sandbox.domain.entities import SandboxConfig

            config = SandboxConfig.from_file(config_path)
        else:
            # No config file — use all-defaults SandboxConfig
            from agent_sandbox.domain.entities import SandboxConfig

            config = SandboxConfig()
    except SandboxError as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_SANDBOX_ERROR)

    # ── Step 2: Build the image spec ────────────────────────────────────────
    try:
        containerfile_content = _load_containerfile()
    except OSError as exc:
        click.echo(f"Failed to read bundled Containerfile: {exc}", err=True)
        sys.exit(EXIT_SANDBOX_ERROR)

    from agent_sandbox.domain.image_spec import ImageSpec

    fingerprint = _compute_fingerprint(containerfile_content)
    image_spec = ImageSpec(base_image=_BASE_IMAGE, tooling_fingerprint=fingerprint)

    # ── Step 3: Wire use cases and run ──────────────────────────────────────
    agent_cmd = [agent] + list(agent_args)

    try:
        run_uc = _build_run_agent_use_case(config)
        result = run_uc.execute(
            config=config,
            image_spec=image_spec,
            containerfile_content=containerfile_content,
            agent_cmd=agent_cmd,
        )
    except SandboxTimeoutError as exc:
        # TimeoutError is a SandboxError subclass — check it first
        click.echo(str(exc), err=True)
        sys.exit(EXIT_TIMEOUT)
    except SandboxError as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_SANDBOX_ERROR)
    except KeyboardInterrupt:
        # SIGINT: cleanup is guaranteed by RunAgentUseCase's try/finally.
        click.echo("Interrupted.", err=True)
        sys.exit(EXIT_SIGINT)

    # ── Step 4: Stream output and exit with inner command's exit code ────────
    if result.stdout:
        click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, err=True, nl=False)

    sys.exit(result.exit_code)
