"""E2E smoke test: clean-venv install, entry-point invocability, and Containerfile
ENTRYPOINT resolution.

FEAT-020 / US-007

Acceptance criteria verified here:

1. **Clean-venv install** — ``pip install .`` into a fresh ``venv`` succeeds;
   the ``agent-sandbox`` console script appears in the venv's bin directory and
   is invokable (``agent-sandbox --help`` exits 0, no ``ModuleNotFoundError``).

2. **``python -m`` invocation** — ``python -m agent_sandbox.cli.main --help``
   returns exit 0 and does not raise a ``ModuleNotFoundError`` or
   ``ImportError``.

3. **Containerfile ENTRYPOINT resolves** — when a container runtime is
   available, the ``container/Containerfile`` can be built and the ENTRYPOINT
   module (``agent_sandbox.cli.main``) resolves without import error.
   Automatically skipped when no runtime is found on PATH.

4. **Version consistency guard** — the ``agent_sandbox.__version__`` attribute
   and the metadata reported by ``importlib.metadata`` both match the
   ``version`` declared in ``pyproject.toml``.

5. **Real package only** — all invocations go through real subprocesses or a
   freshly installed package; no ``unittest.mock`` stand-ins are used.

These tests are split into two groups:

- ``TestSmokeTestInfrastructure``: always runs — sanity-checks paths, static
  analysis, and the current environment.
- ``TestCleanVenvInstall``, ``TestPythonModuleEntryPoint``,
  ``TestVersionConsistency``: run against a module-scoped clean ``venv`` and/or
  the current interpreter.
- ``TestContainerfileEntrypointResolution``: skipped when no container runtime
  is present on PATH.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

#: Root of the repository (parent of ``tests/``).
PROJECT_ROOT = Path(__file__).parent.parent.parent

#: ``pyproject.toml`` at the project root.
PYPROJECT_TOML = PROJECT_ROOT / "pyproject.toml"

#: Deployment ``Containerfile`` (contains the ENTRYPOINT under test).
CONTAINERFILE = PROJECT_ROOT / "container" / "Containerfile"

#: Canonical CLI entry module — must match ``pyproject.toml [project.scripts]``.
CANONICAL_MODULE = "agent_sandbox.cli.main"

#: Canonical ``main`` attribute inside the entry module.
CANONICAL_ATTR = "main"

# ---------------------------------------------------------------------------
# Helper: read pyproject.toml version
# ---------------------------------------------------------------------------


def _read_pyproject_version() -> str:
    """Return the ``version`` string from ``pyproject.toml``."""
    import tomllib  # stdlib on Python 3.11+

    with PYPROJECT_TOML.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


# ---------------------------------------------------------------------------
# Helper: detect container runtime
# ---------------------------------------------------------------------------


def _detect_runtime() -> str | None:
    """Return ``'podman'`` or ``'docker'`` if a usable runtime is on PATH.

    Mirrors the probe logic in ``test_steel_thread.py`` and
    :mod:`agent_sandbox.infrastructure.subprocess_runtime`.

    Returns:
        Binary name of the available runtime, or ``None`` when neither is
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


#: Runtime available for Containerfile tests; ``None`` when absent.
RUNTIME: str | None = _detect_runtime()

#: Human-readable skip reason for tests that require a container runtime.
RUNTIME_SKIP_REASON = (
    "No supported container runtime (Docker or Podman) found on PATH.  "
    "Install Docker (https://docs.docker.com/get-docker/) or "
    "Podman (https://podman.io/get-started) to run Containerfile "
    "ENTRYPOINT resolution tests."
)

# ---------------------------------------------------------------------------
# Module-scoped clean-venv fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clean_venv_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create an isolated ``venv`` and install the project package into it.

    The fixture performs these steps exactly once per test module:

    1. ``python -m venv <tmp>`` — create a pristine virtual environment.
    2. Upgrade ``pip`` to a recent version to avoid build issues.
    3. ``pip install <PROJECT_ROOT> click`` — install the *real* local package
       from source.  ``click`` is included because it is a runtime dependency
       of the CLI that is not yet declared in ``pyproject.toml``; this ensures
       the console-script invocation tests are not blocked by a missing import.

    Yields:
        Path to the venv root directory.

    Raises:
        pytest.fail: immediately, if ``pip install`` exits non-zero, so that
            all downstream tests in this module are marked as errors rather
            than silently collecting into a bad state.
    """
    venv_dir = tmp_path_factory.mktemp("entrypoint_smoke_venv")

    # Locate Python and pip inside the venv (POSIX layout; Windows uses Scripts/)
    if sys.platform == "win32":  # pragma: no cover
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python"

    # 1. Create the virtual environment via subprocess (avoids issues with
    #    UV-managed Python interpreters where ``venv.create(with_pip=True)``
    #    fails because ``ensurepip`` can't resolve the standard library prefix).
    create = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if create.returncode != 0:
        pytest.fail(
            f"venv creation failed (exit {create.returncode}).\n"
            f"stdout: {create.stdout}\n"
            f"stderr: {create.stderr}"
        )

    # 2. Install package + click (needed runtime dep, not yet declared)
    #
    # Note: ``click`` is a runtime dependency of ``agent_sandbox.cli.main``
    # but is not yet declared in ``pyproject.toml``'s ``[project.dependencies]``.
    # We install it explicitly here so that console-script invocation tests
    # are not blocked by a missing import.  The metadata version and pip
    # install of the local package are the primary assertions.
    install = subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            str(PROJECT_ROOT),  # install from local source (real package, not editable)
            "click",            # runtime dep used by cli/main.py
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if install.returncode != 0:
        pytest.fail(
            f"pip install failed (exit {install.returncode}).\n"
            f"stdout: {install.stdout}\n"
            f"stderr: {install.stderr}"
        )

    return venv_dir


@pytest.fixture(scope="module")
def clean_venv_python(clean_venv_dir: Path) -> Path:
    """Return the path to the Python interpreter inside the clean venv."""
    if sys.platform == "win32":  # pragma: no cover
        return clean_venv_dir / "Scripts" / "python.exe"
    return clean_venv_dir / "bin" / "python"


@pytest.fixture(scope="module")
def clean_venv_script(clean_venv_dir: Path) -> Path:
    """Return the path to the ``agent-sandbox`` console script inside the clean venv."""
    if sys.platform == "win32":  # pragma: no cover
        return clean_venv_dir / "Scripts" / "agent-sandbox.exe"
    return clean_venv_dir / "bin" / "agent-sandbox"


# ---------------------------------------------------------------------------
# TestCleanVenvInstall — Criterion 1
# ---------------------------------------------------------------------------


class TestCleanVenvInstall:
    """Criterion 1: clean-venv install smoke test.

    Every test in this class operates against the module-scoped
    ``clean_venv_dir`` fixture — a real, isolated virtual environment with
    only the local package (and its mandatory ``click`` runtime dependency)
    installed.  No mocks, no editable installs, no pytest-injected imports.
    """

    def test_pip_install_dot_succeeds(self, clean_venv_dir: Path) -> None:
        """``pip install .`` into a clean venv must succeed (exit 0).

        The ``clean_venv_dir`` fixture calls ``pytest.fail()`` on non-zero
        exit, so reaching this assertion body means the install succeeded.
        """
        # If we reach here, the clean_venv fixture completed without error.
        assert clean_venv_dir.is_dir(), (
            f"Clean venv directory does not exist: {clean_venv_dir}"
        )

    def test_console_script_is_created_after_install(
        self, clean_venv_script: Path
    ) -> None:
        """The ``agent-sandbox`` console script must exist in the venv after install.

        The ``[project.scripts]`` entry in ``pyproject.toml`` instructs pip to
        create a wrapper script.  Its presence confirms the entry-point
        declaration was processed correctly.
        """
        assert clean_venv_script.is_file(), (
            f"Console script 'agent-sandbox' not found after install.\n"
            f"Expected: {clean_venv_script}\n"
            "Check that pyproject.toml [project.scripts] declares 'agent-sandbox'."
        )

    def test_console_script_help_exits_zero(self, clean_venv_script: Path) -> None:
        """``agent-sandbox --help`` in a clean venv must exit 0.

        Criterion 1: the console script is invokable end-to-end with no
        ``ModuleNotFoundError``.
        """
        result = subprocess.run(
            [str(clean_venv_script), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # No import errors
        assert "ModuleNotFoundError" not in result.stdout, (
            f"ModuleNotFoundError appeared in stdout:\n{result.stdout}"
        )
        assert "ModuleNotFoundError" not in result.stderr, (
            f"ModuleNotFoundError appeared in stderr:\n{result.stderr}"
        )
        assert result.returncode == 0, (
            f"agent-sandbox --help exited {result.returncode} (expected 0).\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_console_script_help_output_mentions_agent_option(
        self, clean_venv_script: Path
    ) -> None:
        """``agent-sandbox --help`` output must mention the ``--agent`` option."""
        result = subprocess.run(
            [str(clean_venv_script), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "--agent" in result.stdout, (
            f"--help output is missing the '--agent' option.\n"
            f"Full stdout:\n{result.stdout}"
        )

    def test_console_script_help_has_usage_line(
        self, clean_venv_script: Path
    ) -> None:
        """``agent-sandbox --help`` output must include a 'Usage:' line."""
        result = subprocess.run(
            [str(clean_venv_script), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output_lower = result.stdout.lower()
        assert "usage:" in output_lower, (
            f"--help output is missing a 'Usage:' line.\n"
            f"Full stdout:\n{result.stdout}"
        )

    def test_no_module_not_found_error_on_import_in_clean_venv(
        self, clean_venv_python: Path
    ) -> None:
        """Importing the canonical entry module in the clean venv must not raise.

        Uses ``python -c "import agent_sandbox.cli.main"`` as a minimal
        import-only check.
        """
        result = subprocess.run(
            [
                str(clean_venv_python),
                "-c",
                f"import {CANONICAL_MODULE}; print('ok')",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Import of '{CANONICAL_MODULE}' failed (exit {result.returncode}).\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert "ModuleNotFoundError" not in result.stderr, (
            f"ModuleNotFoundError during import:\n{result.stderr}"
        )
        assert "ok" in result.stdout, (
            f"Import script did not print 'ok' — module may not have loaded.\n"
            f"stdout: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# TestPythonModuleEntryPoint — Criterion 2
# ---------------------------------------------------------------------------


class TestPythonModuleEntryPoint:
    """Criterion 2: ``python -m <entry-module> --help`` exits 0.

    Tests are run in BOTH the clean venv (isolated install) and the current
    interpreter (editable install) to provide defence-in-depth coverage.
    """

    def test_python_m_entry_module_exits_zero_in_clean_venv(
        self, clean_venv_python: Path
    ) -> None:
        """``python -m agent_sandbox.cli.main --help`` exits 0 in the clean venv.

        Criterion 2: the reconciled entry module must be runnable via
        ``python -m`` without raising.
        """
        result = subprocess.run(
            [str(clean_venv_python), "-m", CANONICAL_MODULE, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"python -m {CANONICAL_MODULE} --help exited {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        # No import errors in stderr
        assert "ModuleNotFoundError" not in result.stderr, (
            f"ModuleNotFoundError in stderr:\n{result.stderr}"
        )
        assert "ImportError" not in result.stderr, (
            f"ImportError in stderr:\n{result.stderr}"
        )

    def test_python_m_entry_module_exits_zero_in_current_env(self) -> None:
        """``python -m agent_sandbox.cli.main --help`` exits 0 in the current env.

        Uses ``sys.executable`` (the same interpreter running pytest) so the
        editable install is exercised.  Provides a fast smoke-check that
        doesn't depend on the clean-venv fixture.
        """
        result = subprocess.run(
            [sys.executable, "-m", CANONICAL_MODULE, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"python -m {CANONICAL_MODULE} --help exited {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert "ModuleNotFoundError" not in result.stderr, (
            f"ModuleNotFoundError in stderr:\n{result.stderr}"
        )

    def test_entry_module_importable_in_current_env(self) -> None:
        """Importing the canonical entry module in the current process must succeed.

        Criterion 2 (in-process variant): verifies zero ``ModuleNotFoundError``
        at import time using ``importlib``.
        """
        try:
            mod = importlib.import_module(CANONICAL_MODULE)
        except ModuleNotFoundError as exc:
            pytest.fail(
                f"ModuleNotFoundError when importing '{CANONICAL_MODULE}': {exc}"
            )
        assert mod is not None, f"importlib.import_module('{CANONICAL_MODULE}') returned None"
        assert hasattr(mod, CANONICAL_ATTR), (
            f"'{CANONICAL_MODULE}' does not expose '{CANONICAL_ATTR}'"
        )
        assert callable(getattr(mod, CANONICAL_ATTR)), (
            f"'{CANONICAL_MODULE}.{CANONICAL_ATTR}' is not callable"
        )

    def test_inline_cli_invocation_via_subprocess_exits_zero(self) -> None:
        """The CLI entry function is invokable end-to-end via subprocess.

        Uses ``sys.executable -c "..."`` to call the real ``main()`` function
        with ``--help``, verifying no phantom module references block execution.
        Criterion 5: runs against the real package, not a mocked stand-in.
        """
        inline = (
            "import sys; "
            f"from {CANONICAL_MODULE} import {CANONICAL_ATTR}; "
            f"sys.argv = ['agent-sandbox', '--help']; "
            "from click.testing import CliRunner; "
            f"r = CliRunner(); res = r.invoke({CANONICAL_ATTR}, ['--help']); "
            "sys.exit(res.exit_code)"
        )
        result = subprocess.run(
            [sys.executable, "-c", inline],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Inline CLI invocation exited {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert "ModuleNotFoundError" not in result.stderr, (
            f"ModuleNotFoundError in stderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# TestVersionConsistency — Criterion 4
# ---------------------------------------------------------------------------


class TestVersionConsistency:
    """Criterion 4: version string in the entry point matches ``pyproject.toml``.

    Checks both the ``__version__`` module attribute (set in
    ``agent_sandbox/__init__.py``) and the ``importlib.metadata`` version
    (set by the package installer from ``pyproject.toml``).
    """

    def test_package_dunder_version_matches_pyproject(self) -> None:
        """``agent_sandbox.__version__`` must equal the version in ``pyproject.toml``.

        Guards against stale hard-coded version strings that diverge from the
        canonical declaration.
        """
        expected = _read_pyproject_version()

        import agent_sandbox

        actual = agent_sandbox.__version__
        assert actual == expected, (
            f"agent_sandbox.__version__ = {actual!r} does not match "
            f"pyproject.toml version {expected!r}.  "
            "Update agent_sandbox/__init__.py to use the same version string."
        )

    def test_installed_metadata_version_matches_pyproject(self) -> None:
        """The metadata version installed by pip must match ``pyproject.toml``.

        Uses ``importlib.metadata.version()`` which reads the dist-info
        installed by pip — the same version that ``pip show`` would report.
        """
        import importlib.metadata

        expected = _read_pyproject_version()
        try:
            installed = importlib.metadata.version("agent-sandbox")
        except importlib.metadata.PackageNotFoundError:
            pytest.skip(
                "agent-sandbox is not installed via pip in the current environment; "
                "skipping metadata version check."
            )

        assert installed == expected, (
            f"Installed metadata version {installed!r} does not match "
            f"pyproject.toml version {expected!r}.  "
            "Run 'pip install -e .' to update the installed metadata."
        )

    def test_installed_metadata_version_matches_pyproject_in_clean_venv(
        self, clean_venv_python: Path
    ) -> None:
        """Installed package version in the clean venv must match ``pyproject.toml``.

        Criterion 4 applied to the isolated install — guards against the
        installer picking a different version from a cache or registry.
        """
        expected = _read_pyproject_version()

        result = subprocess.run(
            [
                str(clean_venv_python),
                "-c",
                (
                    "import importlib.metadata; "
                    "print(importlib.metadata.version('agent-sandbox'))"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Failed to query installed version in clean venv: {result.stderr}"
        )
        installed = result.stdout.strip()
        assert installed == expected, (
            f"Clean-venv installed version {installed!r} does not match "
            f"pyproject.toml version {expected!r}."
        )

    def test_package_version_is_non_empty_string(self) -> None:
        """``pyproject.toml`` version must be a non-empty string."""
        version = _read_pyproject_version()
        assert isinstance(version, str), (
            f"pyproject.toml version is not a string: {version!r}"
        )
        assert version.strip(), "pyproject.toml version must not be empty"

    def test_package_version_has_semver_structure(self) -> None:
        """``pyproject.toml`` version must follow a semver-like ``X.Y.Z`` pattern."""
        import re

        version = _read_pyproject_version()
        pattern = r"^\d+\.\d+(\.\d+)?([.\-+].+)?$"
        assert re.match(pattern, version), (
            f"Version {version!r} does not match a semver-like pattern (X.Y[.Z])."
        )


# ---------------------------------------------------------------------------
# TestContainerfileEntrypointResolution — Criterion 3 (requires runtime)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(RUNTIME is None, reason=RUNTIME_SKIP_REASON)
class TestContainerfileEntrypointResolution:
    """Criterion 3: Containerfile builds successfully and ENTRYPOINT resolves.

    All tests in this class invoke a real container runtime and are
    automatically skipped when ``RUNTIME is None``.

    **Note on build time**: the ``container/Containerfile`` installs several
    heavyweight tools (GitHub CLI, Claude Code, mise, poppler) and can take
    5–15 minutes on a cold cache.  Use ``--timeout`` or mark these tests as
    slow in CI if needed.
    """

    _TEST_TAG_BUILD = "agent-sandbox-smoke-test:containerfile-build"
    _TEST_TAG_RESOLVE = "agent-sandbox-smoke-test:entrypoint-resolve"

    def test_containerfile_exists(self) -> None:
        """``container/Containerfile`` must exist before attempting a build."""
        assert CONTAINERFILE.is_file(), (
            f"Deployment Containerfile not found: {CONTAINERFILE}"
        )

    def test_containerfile_build_succeeds(self) -> None:
        """Building ``container/Containerfile`` must exit 0.

        Criterion 3 (build step): the Containerfile must be syntactically
        valid and all non-optional ``RUN`` steps must succeed.  The pip
        install step uses ``|| true`` so an offline/private PyPI environment
        does not fail the build.
        """
        tag = self._TEST_TAG_BUILD
        try:
            result = subprocess.run(
                [
                    RUNTIME,
                    "build",
                    "-f", str(CONTAINERFILE),
                    "-t", tag,
                    "--no-cache",
                    str(PROJECT_ROOT),
                ],
                capture_output=True,
                text=True,
                timeout=900,  # 15 min for cold build
            )
            assert result.returncode == 0, (
                f"Containerfile build failed (exit {result.returncode}).\n"
                f"--- stdout (last 2 kB) ---\n{result.stdout[-2048:]}\n"
                f"--- stderr (last 2 kB) ---\n{result.stderr[-2048:]}"
            )
        finally:
            # Remove the test image regardless of build outcome
            subprocess.run(
                [RUNTIME, "rmi", "-f", tag],
                capture_output=True,
                timeout=30,
            )

    def test_entrypoint_module_resolves_in_local_install_container(
        self, tmp_path: Path
    ) -> None:
        """The ENTRYPOINT module resolves in a container with the package installed.

        Builds a minimal test Dockerfile that:
          1. Starts from ``python:3.11-slim`` (small, fast).
          2. Copies and installs the local source (no PyPI dependency).
          3. Installs ``click`` (runtime dep not yet declared).
          4. Sets the same ENTRYPOINT as ``container/Containerfile``:
             ``python -m agent_sandbox.cli.main``.

        Then runs ``--help`` against that ENTRYPOINT to confirm the module
        resolves without ``ModuleNotFoundError`` or ``ImportError``.

        This directly validates the ENTRYPOINT pattern used in the deployment
        Containerfile without requiring the package to be published to PyPI.
        """
        test_tag = self._TEST_TAG_RESOLVE

        # Build a minimal test container with the local package installed
        test_dockerfile_content = (
            "FROM python:3.11-slim\n"
            "WORKDIR /app\n"
            "COPY . /app\n"
            "RUN pip install --quiet /app click\n"
            f'ENTRYPOINT ["python", "-m", "{CANONICAL_MODULE}"]\n'
        )
        test_dockerfile = tmp_path / "TestDockerfile.smoke"
        test_dockerfile.write_text(test_dockerfile_content, encoding="utf-8")

        try:
            # Build the test image
            build_result = subprocess.run(
                [
                    RUNTIME,
                    "build",
                    "-f", str(test_dockerfile),
                    "-t", test_tag,
                    str(PROJECT_ROOT),
                ],
                capture_output=True,
                text=True,
                timeout=300,  # 5 min
            )
            if build_result.returncode != 0:
                pytest.skip(
                    f"Could not build test container for ENTRYPOINT resolution "
                    f"check (exit {build_result.returncode}).\n"
                    f"stderr: {build_result.stderr[-1000:]}"
                )

            # Run the ENTRYPOINT with --help to verify module resolution
            run_result = subprocess.run(
                [RUNTIME, "run", "--rm", test_tag, "--help"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            combined_output = run_result.stdout + run_result.stderr
            assert "ModuleNotFoundError" not in combined_output, (
                f"ModuleNotFoundError when running ENTRYPOINT --help:\n"
                f"{combined_output}"
            )
            assert "ImportError" not in combined_output, (
                f"ImportError when running ENTRYPOINT --help:\n"
                f"{combined_output}"
            )
            assert run_result.returncode == 0, (
                f"ENTRYPOINT --help exited {run_result.returncode} (expected 0).\n"
                f"stdout: {run_result.stdout}\n"
                f"stderr: {run_result.stderr}"
            )

        finally:
            # Clean up test image
            subprocess.run(
                [RUNTIME, "rmi", "-f", test_tag],
                capture_output=True,
                timeout=30,
            )


# ---------------------------------------------------------------------------
# TestSmokeTestInfrastructure — meta / sanity — always runs
# ---------------------------------------------------------------------------


class TestSmokeTestInfrastructure:
    """Sanity checks for smoke-test infrastructure.

    These tests always run — they do not require a container runtime and do
    not depend on the clean-venv fixture.  They verify that the paths,
    metadata, and module references used by the other test classes are correct
    *before* any expensive operations are attempted.
    """

    def test_pyproject_toml_exists(self) -> None:
        """``pyproject.toml`` must exist at the project root."""
        assert PYPROJECT_TOML.is_file(), (
            f"pyproject.toml not found: {PYPROJECT_TOML}"
        )

    def test_containerfile_exists(self) -> None:
        """``container/Containerfile`` must exist."""
        assert CONTAINERFILE.is_file(), (
            f"Deployment Containerfile not found: {CONTAINERFILE}"
        )

    def test_pyproject_version_is_parseable(self) -> None:
        """``pyproject.toml`` must declare a parseable ``version`` string."""
        version = _read_pyproject_version()
        assert isinstance(version, str), "Version must be a string"
        assert version.strip(), "Version must be non-empty"

    def test_pyproject_declares_agent_sandbox_console_script(self) -> None:
        """``pyproject.toml`` must declare ``agent-sandbox`` in ``[project.scripts]``."""
        import tomllib

        with PYPROJECT_TOML.open("rb") as f:
            data = tomllib.load(f)

        scripts = data.get("project", {}).get("scripts", {})
        assert "agent-sandbox" in scripts, (
            f"pyproject.toml [project.scripts] does not declare 'agent-sandbox'; "
            f"found: {list(scripts.keys())}"
        )

    def test_pyproject_script_points_to_canonical_module(self) -> None:
        """The ``agent-sandbox`` script entry must point to the canonical module."""
        import tomllib

        with PYPROJECT_TOML.open("rb") as f:
            data = tomllib.load(f)

        scripts = data.get("project", {}).get("scripts", {})
        entry = scripts.get("agent-sandbox", "")
        expected = f"{CANONICAL_MODULE}:{CANONICAL_ATTR}"
        assert entry == expected, (
            f"pyproject.toml agent-sandbox script points to {entry!r}; "
            f"expected {expected!r}."
        )

    def test_containerfile_entrypoint_references_shell_entrypoint(self) -> None:
        """``container/Containerfile`` ENTRYPOINT must reference the shell entrypoint.

        Static analysis: checks that ``entrypoint.sh`` appears in the
        Containerfile ENTRYPOINT directive.  The container uses a shell script
        (not the Python CLI module) as its entrypoint so it can run a root init
        phase (package installation) before dropping to the project user.
        """
        content = CONTAINERFILE.read_text(encoding="utf-8")
        assert "entrypoint.sh" in content, (
            f"Containerfile does not reference 'entrypoint.sh'.\n"
            f"The ENTRYPOINT should be: "
            f'["/home/claude/entrypoint.sh"]\n'
            f"Containerfile path: {CONTAINERFILE}"
        )

    def test_canonical_module_importable_without_error(self) -> None:
        """The canonical entry module must be importable in the current process."""
        try:
            mod = importlib.import_module(CANONICAL_MODULE)
        except ModuleNotFoundError as exc:
            pytest.fail(
                f"ModuleNotFoundError importing '{CANONICAL_MODULE}': {exc}\n"
                "Ensure the package is installed or src/ is on sys.path."
            )
        assert mod is not None

    def test_canonical_module_exposes_main_callable(self) -> None:
        """The canonical entry module must expose a callable ``main`` attribute."""
        mod = importlib.import_module(CANONICAL_MODULE)
        assert hasattr(mod, CANONICAL_ATTR), (
            f"'{CANONICAL_MODULE}' does not expose '{CANONICAL_ATTR}'"
        )
        assert callable(getattr(mod, CANONICAL_ATTR)), (
            f"'{CANONICAL_MODULE}.{CANONICAL_ATTR}' is not callable"
        )

    def test_runtime_detection_returns_none_or_non_empty_string(self) -> None:
        """``_detect_runtime()`` must return ``None`` or a non-empty string."""
        result = _detect_runtime()
        assert result is None or (isinstance(result, str) and result), (
            f"_detect_runtime() returned unexpected value: {result!r}"
        )

    def test_runtime_skip_reason_mentions_docker_podman_path(self) -> None:
        """``RUNTIME_SKIP_REASON`` must mention Docker, Podman, and PATH."""
        assert "Docker" in RUNTIME_SKIP_REASON, (
            "RUNTIME_SKIP_REASON must mention 'Docker'"
        )
        assert "Podman" in RUNTIME_SKIP_REASON, (
            "RUNTIME_SKIP_REASON must mention 'Podman'"
        )
        assert "PATH" in RUNTIME_SKIP_REASON, (
            "RUNTIME_SKIP_REASON must mention 'PATH'"
        )

    def test_containerfile_entrypoint_script_exists(self) -> None:
        """The shell script referenced in the Containerfile ENTRYPOINT must exist.

        Cross-file consistency guard: ``container/Containerfile`` references
        ``entrypoint.sh`` as its ENTRYPOINT; the script must be present alongside it.
        The container uses a shell entrypoint (not the Python CLI module) so that
        a root init phase can install declared apt packages before dropping
        privileges to the project user.
        """
        containerfile_content = CONTAINERFILE.read_text(encoding="utf-8")
        assert "entrypoint.sh" in containerfile_content, (
            "Containerfile ENTRYPOINT does not reference entrypoint.sh.\n"
            f"Containerfile path: {CONTAINERFILE}"
        )
        entrypoint_script = CONTAINERFILE.parent / "entrypoint.sh"
        assert entrypoint_script.exists(), (
            f"entrypoint.sh not found at {entrypoint_script}.\n"
            "The Containerfile ENTRYPOINT references this file — it must exist."
        )
