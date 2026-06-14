"""End-to-end smoke test: complete steel-thread from CLI entry to container exit.

FEAT-010 / US-007

This module exercises the FULL agent-sandbox stack against a *real* container
runtime (Docker or Podman).  Nothing is mocked — real images are built or
reused, real containers are started and stopped, and real processes are run
inside them.

**Automatic skip (Criterion 4)**: ``TestSteelThread`` is decorated with
``@pytest.mark.skipif`` at the class level so it is skipped with a clear
message when no runtime is found on PATH.  The ``SKIP_REASON`` constant names
the missing binaries and links to install docs.

``TestInfrastructure`` (sanity / meta tests) is NOT gated — it always runs
so the fixtures and helpers can be verified on every platform.

Criteria exercised:
  1. CLI executes a command inside a freshly-started container and captures
     its stdout, stderr, and exit code
     (``TestSteelThread.test_cli_executes_command_in_container``).
  2. On a second run the previously-built image is reused — no rebuild
     (``TestSteelThread.test_image_is_reused_on_second_run``).
  3. No orphaned container remains after the run — verified by querying the
     runtime's container list
     (``TestSteelThread.test_no_orphaned_container_after_run``).
  4. Test is automatically skipped when no runtime is available
     (``@pytest.mark.skipif`` on ``TestSteelThread``).
  5. CLI exit code mirrors the inner command's exit code exactly
     (``TestSteelThread.test_exit_code_propagates_from_inner_command``).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

#: Directory containing E2E fixture files (e.g. ``.agent-sandbox``).
FIXTURE_DIR = Path(__file__).parent / "fixtures"

#: Repository root — used to locate the bundled Containerfile.
PROJECT_ROOT = Path(__file__).parent.parent.parent

#: Path to the bundled Containerfile (same file the CLI always uses).
_CONTAINERFILE_PATH = (
    PROJECT_ROOT / "src" / "agent_sandbox" / "infrastructure" / "Containerfile"
)

# ---------------------------------------------------------------------------
# Runtime detection — Criterion 4
# ---------------------------------------------------------------------------


def _detect_runtime() -> str | None:
    """Return ``'podman'`` or ``'docker'`` if a usable runtime is on PATH.

    Probes each candidate binary by running ``--version`` with a short
    timeout.  Podman is preferred (rootless by default) over Docker,
    matching :class:`~agent_sandbox.infrastructure.subprocess_runtime.SubprocessRuntimeAdapter`
    AUTO mode.

    Returns:
        The name of an available runtime binary, or ``None`` when neither is
        found or both probes time out.
    """
    for binary in ("podman", "docker"):
        if shutil.which(binary) is None:
            continue
        try:
            probe = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                timeout=5,
            )
            if probe.returncode == 0:
                return binary
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


#: Resolved runtime binary name, or ``None`` when no runtime is available.
RUNTIME: str | None = _detect_runtime()

#: Human-readable skip reason shown in pytest output when RUNTIME is None.
#: Criterion 4: must be clear and actionable.
SKIP_REASON = (
    "No supported container runtime (Docker or Podman) found on PATH.  "
    "Install Docker (https://docs.docker.com/get-docker/) or "
    "Podman (https://podman.io/get-started) and ensure the binary is "
    "on your PATH to run the E2E steel-thread smoke tests."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_expected_image_tag() -> str:
    """Compute the image tag the CLI will build or reuse.

    Mirrors the fingerprint computation in :mod:`agent_sandbox.cli` and
    :mod:`agent_sandbox.facade` so the test can look up the image in the
    runtime's local registry without running the CLI first.

    Returns:
        The fully-qualified image tag, e.g. ``"agent-sandbox:a1b2c3d4e5f6g7h8"``.
    """
    content = _CONTAINERFILE_PATH.read_text(encoding="utf-8")
    fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()

    from agent_sandbox.domain.image_spec import ImageSpec

    spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint=fingerprint)
    return spec.tag


def _run_cli(
    cli_args: list[str],
    *,
    cwd: Path,
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    """Invoke the ``agent-sandbox`` CLI as a real subprocess.

    Uses :data:`sys.executable` with an inline ``-c`` script so the test
    always uses the same Python environment as pytest itself, even if the
    ``agent-sandbox`` console script is not on PATH.

    Click reads ``sys.argv[1:]`` inside the subprocess.  Because Python puts
    the inline script in ``sys.argv[0]`` and extra positional items after the
    ``-c <script>`` in ``sys.argv[1:]``, the *cli_args* become exactly the
    arguments Click processes — identical to typing
    ``agent-sandbox <cli_args>`` at a shell prompt.

    Args:
        cli_args: Arguments forwarded to the CLI, e.g.
            ``["--agent", "echo", "hello"]``.
        cwd: Working directory for the subprocess.  The CLI loads
            ``.agent-sandbox`` from ``cwd`` (or uses defaults if absent).
        timeout: Wall-clock timeout in seconds.  Use ≥ 600 s when the image
            may need to be built from scratch.

    Returns:
        :class:`subprocess.CompletedProcess` with ``returncode``, ``stdout``,
        and ``stderr`` populated.
    """
    inline_script = "from agent_sandbox.cli import main; main()"
    cmd = [sys.executable, "-c", inline_script] + list(cli_args)
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _image_exists(runtime: str, image_tag: str) -> bool:
    """Return ``True`` if *image_tag* is present in the local image registry.

    Uses ``<runtime> inspect --type image <tag>`` which exits 0 on hit and
    non-zero on miss — the same approach used by
    :class:`~agent_sandbox.infrastructure.image_builder.ContainerfileImageBuilder.is_cached`.

    Args:
        runtime: Container runtime binary name (``"docker"`` or ``"podman"``).
        image_tag: Fully-qualified image tag to probe.

    Returns:
        ``True`` when the image is in the local cache, ``False`` otherwise.
    """
    result = subprocess.run(
        [runtime, "inspect", "--type", "image", image_tag],
        capture_output=True,
        timeout=15,
    )
    return result.returncode == 0


def _list_all_containers_for_image(runtime: str, image_tag: str) -> list[str]:
    """Return all container IDs (running *and* stopped) launched from *image_tag*.

    Used to verify no orphaned container remains after the CLI exits.
    Both ``--rm`` semantics and the explicit ``rm -f`` in
    :meth:`~agent_sandbox.infrastructure.container_adapter.CliContainerHandle.stop`
    ensure this list is empty after a clean run.

    Args:
        runtime: Container runtime binary name.
        image_tag: Image tag to filter containers by.

    Returns:
        List of container IDs.  An empty list means no orphans remain.
    """
    result = subprocess.run(
        [runtime, "ps", "-aq", "--filter", f"ancestor={image_tag}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Return a temp directory pre-populated with the E2E ``.agent-sandbox`` fixture.

    Copies ``tests/e2e/fixtures/.agent-sandbox`` into *tmp_path* so that
    the CLI discovers it when run with ``cwd=tmp_path``.
    """
    shutil.copy(FIXTURE_DIR / ".agent-sandbox", tmp_path / ".agent-sandbox")
    return tmp_path


# ---------------------------------------------------------------------------
# E2E tests — skipped when no runtime is available (Criterion 4)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(RUNTIME is None, reason=SKIP_REASON)
class TestSteelThread:
    """End-to-end smoke tests for the full agent-sandbox steel thread.

    All tests in this class invoke the real CLI against the real container
    runtime.  They are automatically skipped when ``RUNTIME is None``
    (Criterion 4).

    **Timeout note**: the first test to run on a cold cache may need to build
    the sandbox image from scratch.  The bundled Containerfile installs
    several tools and can take 5–10 minutes.  Subsequent tests reuse the
    cached image and complete in seconds.
    """

    # ------------------------------------------------------------------
    # Criterion 1: CLI executes command in container
    # ------------------------------------------------------------------

    def test_cli_executes_command_in_container(self, project_dir: Path) -> None:
        """Criterion 1: CLI runs a command in a container and returns its output.

        Invokes ``agent-sandbox --agent echo hello_steel_thread`` from a
        temp project dir.  Verifies:
        - The subprocess exits with code 0.
        - The captured stdout contains the echoed text.
        """
        result = _run_cli(
            ["--agent", "echo", "hello_steel_thread"],
            cwd=project_dir,
            timeout=600,  # Allow up to 10 min for a cold image build
        )

        assert result.returncode == 0, (
            f"CLI exited with code {result.returncode} (expected 0).\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )
        assert "hello_steel_thread" in result.stdout, (
            f"Expected 'hello_steel_thread' in CLI stdout.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )

    # ------------------------------------------------------------------
    # Criterion 2: Cached image reused on second run
    # ------------------------------------------------------------------

    def test_image_is_reused_on_second_run(self, project_dir: Path) -> None:
        """Criterion 2: Second CLI run reuses the cached image (no rebuild).

        After the first run builds (or uses) the image, the image must be
        present in the local registry.  The second run uses a tighter
        timeout (120 s) that is sufficient to start a cached container but
        far shorter than a cold image build — making a rebuild observable
        as a timeout failure.
        """
        image_tag = _get_expected_image_tag()

        # --- First run: build image if not cached ---
        result1 = _run_cli(
            ["--agent", "echo", "first_run"],
            cwd=project_dir,
            timeout=600,
        )
        assert result1.returncode == 0, (
            f"First CLI run failed (exit {result1.returncode}).\n"
            f"stdout: {result1.stdout!r}\n"
            f"stderr: {result1.stderr!r}"
        )
        assert "first_run" in result1.stdout, (
            f"Expected 'first_run' in first-run stdout: {result1.stdout!r}"
        )

        # Image must now be in the local registry.
        assert _image_exists(RUNTIME, image_tag), (
            f"Image '{image_tag}' not found in local registry after first run.  "
            f"The CLI should have built and tagged it.  RUNTIME={RUNTIME!r}"
        )

        # --- Second run: must complete within the shorter timeout ---
        # 120 s is enough to start a pre-built container and echo a string,
        # but NOT enough to build the full image from scratch.  A timeout
        # here would indicate the image was not reused.
        result2 = _run_cli(
            ["--agent", "echo", "second_run"],
            cwd=project_dir,
            timeout=120,
        )
        assert result2.returncode == 0, (
            f"Second CLI run failed (exit {result2.returncode}) — "
            f"image cache may not have been reused.\n"
            f"stdout: {result2.stdout!r}\n"
            f"stderr: {result2.stderr!r}"
        )
        assert "second_run" in result2.stdout, (
            f"Expected 'second_run' in second-run stdout: {result2.stdout!r}"
        )

    # ------------------------------------------------------------------
    # Criterion 3: No orphaned container after run
    # ------------------------------------------------------------------

    def test_no_orphaned_container_after_run(self, project_dir: Path) -> None:
        """Criterion 3: No orphaned container remains after the CLI exits.

        Queries the runtime for ALL containers (running and stopped) from
        the sandbox image immediately after the CLI process returns.
        An empty result confirms that ``--rm`` and/or the explicit
        ``stop`` + ``rm -f`` cleanup in
        :meth:`~agent_sandbox.infrastructure.container_adapter.CliContainerHandle.stop`
        removed the container before the process exited.
        """
        image_tag = _get_expected_image_tag()

        result = _run_cli(
            ["--agent", "echo", "cleanup_check"],
            cwd=project_dir,
            timeout=600,
        )
        assert result.returncode == 0, (
            f"CLI failed (exit {result.returncode}) before orphan check.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )

        orphans = _list_all_containers_for_image(RUNTIME, image_tag)
        assert len(orphans) == 0, (
            f"Orphaned container(s) remain after CLI exit: {orphans}\n"
            f"The CLI must clean up all containers via try/finally + --rm.\n"
            f"image_tag={image_tag!r}  RUNTIME={RUNTIME!r}"
        )

    # ------------------------------------------------------------------
    # Criterion 5: Exit code propagation
    # ------------------------------------------------------------------

    def test_exit_code_propagates_from_inner_command(self, project_dir: Path) -> None:
        """Criterion 5: CLI process exit code mirrors the inner command's exit code.

        Runs ``sh -c "exit 42"`` inside the container.  The CLI must exit
        with code 42, not 0 or a fixed failure code.

        This verifies that :mod:`agent_sandbox.cli` calls
        ``sys.exit(result.exit_code)`` with the actual inner exit code.
        """
        result = _run_cli(
            ["--agent", "sh", "-c", "exit 42"],
            cwd=project_dir,
            timeout=600,
        )

        assert result.returncode == 42, (
            f"Expected CLI exit code 42 (inner command 'sh -c exit 42'), "
            f"got {result.returncode}.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )

    # ------------------------------------------------------------------
    # Bonus: stderr forwarded from container
    # ------------------------------------------------------------------

    def test_stderr_from_container_is_forwarded(self, project_dir: Path) -> None:
        """CLI forwards stderr from the container exec to its own stderr stream.

        Runs a command that writes to stderr inside the container and verifies
        that text reaches the CLI process stderr.
        """
        result = _run_cli(
            ["--agent", "sh", "-c", "echo container_stderr >&2; exit 0"],
            cwd=project_dir,
            timeout=600,
        )

        assert result.returncode == 0, (
            f"CLI exited with {result.returncode}.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )
        assert "container_stderr" in result.stderr, (
            f"Expected 'container_stderr' in CLI stderr stream.\n"
            f"stderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Infrastructure / meta tests — NOT gated by runtime availability
# ---------------------------------------------------------------------------


class TestInfrastructure:
    """Sanity checks for E2E test infrastructure.  Always run (no skip guard).

    These tests verify that the helpers, fixtures, and skip mechanism are
    correctly implemented, independent of whether a container runtime is
    installed.
    """

    def test_skip_reason_is_descriptive(self) -> None:
        """SKIP_REASON must mention Docker, Podman, and PATH (Criterion 4)."""
        assert "Docker" in SKIP_REASON, (
            "SKIP_REASON must mention Docker so operators know what to install"
        )
        assert "Podman" in SKIP_REASON, (
            "SKIP_REASON must mention Podman so operators know what to install"
        )
        assert "PATH" in SKIP_REASON, (
            "SKIP_REASON must mention PATH — the most common misconfiguration"
        )

    def test_runtime_detection_returns_none_or_non_empty_string(self) -> None:
        """_detect_runtime() must return None or a non-empty string."""
        result = _detect_runtime()
        assert result is None or (isinstance(result, str) and len(result) > 0), (
            f"_detect_runtime() must return None or a non-empty string, got {result!r}"
        )

    def test_fixture_config_file_exists(self) -> None:
        """The E2E fixture config file must exist at the expected path."""
        fixture = FIXTURE_DIR / ".agent-sandbox"
        assert fixture.is_file(), (
            f"Fixture config not found: {fixture}\n"
            f"Create tests/e2e/fixtures/.agent-sandbox with minimal config."
        )

    def test_fixture_config_is_parseable_by_sandbox_config(self) -> None:
        """The fixture ``.agent-sandbox`` must be parseable by ``SandboxConfig.from_file``."""
        from agent_sandbox.domain.entities import SandboxConfig

        fixture = FIXTURE_DIR / ".agent-sandbox"
        config = SandboxConfig.from_file(fixture)
        assert config is not None
        assert isinstance(config, SandboxConfig), (
            f"Expected SandboxConfig, got {type(config).__name__!r}"
        )

    def test_expected_image_tag_is_deterministic(self) -> None:
        """``_get_expected_image_tag()`` must return the same tag on every call."""
        tag1 = _get_expected_image_tag()
        tag2 = _get_expected_image_tag()
        assert tag1 == tag2, (
            f"Image tag is not deterministic; got {tag1!r} then {tag2!r}"
        )
        assert tag1.startswith("agent-sandbox:"), (
            f"Image tag must start with 'agent-sandbox:', got {tag1!r}"
        )

    def test_run_cli_helper_returns_completed_process(self, tmp_path: Path) -> None:
        """``_run_cli()`` must invoke a real subprocess and return CompletedProcess.

        Runs the CLI with ``--agent echo smoke_test`` in a tmp dir (no
        ``.agent-sandbox`` — CLI falls back to defaults).  The test only
        checks that a :class:`subprocess.CompletedProcess` is returned with
        integer ``returncode`` — it does NOT assert exit code 0, because
        without a runtime the CLI exits with 2 (EXIT_SANDBOX_ERROR).
        """
        result = _run_cli(
            ["--agent", "echo", "smoke_test"],
            cwd=tmp_path,
            timeout=30,
        )
        assert hasattr(result, "returncode"), (
            "_run_cli() must return a CompletedProcess with 'returncode'"
        )
        assert hasattr(result, "stdout"), (
            "_run_cli() must return a CompletedProcess with 'stdout'"
        )
        assert hasattr(result, "stderr"), (
            "_run_cli() must return a CompletedProcess with 'stderr'"
        )
        assert isinstance(result.returncode, int), (
            f"returncode must be an int, got {type(result.returncode).__name__!r}"
        )
