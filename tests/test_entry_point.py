"""Tests for FEAT-019: Reconcile and implement the canonical console-script entry point.

TDD: tests written before implementation.

Acceptance criteria:
  1. Importing the canonical entry module succeeds with no ModuleNotFoundError
  2. The module exposes a callable `main` referenced by the [project.scripts] entry
     in pyproject.toml
  3. pyproject.toml console-script target and the Containerfile ENTRYPOINT reference
     the same, existing module path (parametrized assertion over both files)
  4. `agent-sandbox --help` exits 0 and prints usage including --agent
  5. A unit test parses pyproject.toml and asserts the declared entry-point module/attr
     is importable and callable
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

SRC_DIR = Path(__file__).parent.parent / "src"
PYPROJECT_TOML = Path(__file__).parent.parent / "pyproject.toml"
CONTAINERFILE = Path(__file__).parent.parent / "container" / "Containerfile"

# The canonical entry module — set by this feature (FEAT-019)
CANONICAL_MODULE = "agent_sandbox.cli.main"
CANONICAL_ATTR = "main"
CANONICAL_ENTRY = f"{CANONICAL_MODULE}:{CANONICAL_ATTR}"

# Physical path of the canonical module file
CANONICAL_MODULE_FILE = SRC_DIR / "agent_sandbox" / "cli" / "main.py"
CLI_PACKAGE_INIT = SRC_DIR / "agent_sandbox" / "cli" / "__init__.py"


# ---------------------------------------------------------------------------
# 1. File existence — physical module must exist on disk
# ---------------------------------------------------------------------------


class TestCanonicalModuleFilesExist:
    """The cli/ package files must exist on disk as physical files."""

    def test_cli_main_py_exists(self):
        """src/agent_sandbox/cli/main.py must exist on disk."""
        assert CANONICAL_MODULE_FILE.is_file(), (
            f"Canonical entry module missing: {CANONICAL_MODULE_FILE}"
        )

    def test_cli_init_py_exists(self):
        """src/agent_sandbox/cli/__init__.py must exist on disk."""
        assert CLI_PACKAGE_INIT.is_file(), (
            f"cli package __init__.py missing: {CLI_PACKAGE_INIT}"
        )

    def test_cli_is_a_package_directory(self):
        """src/agent_sandbox/cli/ must be a directory (a package, not a module file)."""
        cli_dir = SRC_DIR / "agent_sandbox" / "cli"
        assert cli_dir.is_dir(), (
            f"cli/ must be a package directory, not a flat module file; "
            f"expected directory at {cli_dir}"
        )


# ---------------------------------------------------------------------------
# 2. Importability — criterion 1
# ---------------------------------------------------------------------------


class TestCanonicalModuleImportable:
    """Criterion 1: Importing agent_sandbox.cli.main succeeds."""

    def test_cli_main_module_importable(self):
        """agent_sandbox.cli.main must be importable without ModuleNotFoundError."""
        try:
            import agent_sandbox.cli.main  # noqa: F401
        except ModuleNotFoundError as exc:
            pytest.fail(
                f"ModuleNotFoundError importing agent_sandbox.cli.main: {exc}"
            )

    def test_cli_package_importable(self):
        """agent_sandbox.cli (the package) must be importable."""
        try:
            import agent_sandbox.cli  # noqa: F401
        except ModuleNotFoundError as exc:
            pytest.fail(
                f"ModuleNotFoundError importing agent_sandbox.cli: {exc}"
            )

    def test_no_import_error_on_entry_module(self):
        """importlib.import_module must not raise for the canonical entry module."""
        mod = importlib.import_module(CANONICAL_MODULE)
        assert mod is not None


# ---------------------------------------------------------------------------
# 3. callable main — criterion 2
# ---------------------------------------------------------------------------


class TestMainCallable:
    """Criterion 2: The canonical entry module exposes a callable `main`."""

    def test_cli_main_has_main_function(self):
        """agent_sandbox.cli.main must expose a `main` attribute."""
        import agent_sandbox.cli.main as entry_mod

        assert hasattr(entry_mod, "main"), (
            "agent_sandbox.cli.main must define a `main` callable"
        )

    def test_main_is_callable(self):
        """agent_sandbox.cli.main.main must be callable (Click command)."""
        import agent_sandbox.cli.main as entry_mod

        assert callable(entry_mod.main), (
            f"main is not callable: {type(entry_mod.main)}"
        )

    def test_cli_package_exposes_main_submodule(self):
        """agent_sandbox.cli.main is the canonical submodule (not a re-exported func).

        After the flat-module → package refactoring, ``agent_sandbox.cli.main``
        refers to the *submodule* (cli/main.py).  The callable Click command lives
        at ``agent_sandbox.cli.main.main``.  The package's __init__.py intentionally
        does NOT shadow the submodule by binding the Click command to the name ``main``.
        """
        import agent_sandbox.cli as cli_pkg

        # After importing the submodule, cli.main is the module object, not a callable
        assert hasattr(cli_pkg, "main"), (
            "agent_sandbox.cli must expose a 'main' attribute (the cli.main submodule)"
        )
        import types
        assert isinstance(cli_pkg.main, types.ModuleType), (
            f"agent_sandbox.cli.main must be the cli/main.py submodule, "
            f"not {type(cli_pkg.main)}"
        )
        # The Click command is reachable via the submodule
        assert callable(cli_pkg.main.main), (
            "agent_sandbox.cli.main.main (the Click command) must be callable"
        )


# ---------------------------------------------------------------------------
# 4. pyproject.toml entry-point — criterion 5
# ---------------------------------------------------------------------------


class TestPyprojectEntryPoint:
    """Criterion 5: Parsed pyproject.toml declares an importable, callable entry."""

    def test_pyproject_has_agent_sandbox_script(self):
        """pyproject.toml must declare an [project.scripts] entry 'agent-sandbox'."""
        import tomllib

        with PYPROJECT_TOML.open("rb") as f:
            data = tomllib.load(f)

        scripts = data.get("project", {}).get("scripts", {})
        assert "agent-sandbox" in scripts, (
            f"pyproject.toml [project.scripts] missing 'agent-sandbox'; "
            f"found: {list(scripts.keys())}"
        )

    def test_pyproject_entry_points_to_canonical_module(self):
        """The agent-sandbox script must point to agent_sandbox.cli.main:main."""
        import tomllib

        with PYPROJECT_TOML.open("rb") as f:
            data = tomllib.load(f)

        scripts = data.get("project", {}).get("scripts", {})
        entry = scripts.get("agent-sandbox", "")
        module_path, _, attr = entry.partition(":")
        assert module_path == CANONICAL_MODULE, (
            f"Expected entry-point module '{CANONICAL_MODULE}', got '{module_path}'"
        )
        assert attr == CANONICAL_ATTR, (
            f"Expected entry-point attr '{CANONICAL_ATTR}', got '{attr}'"
        )

    def test_entry_point_module_is_importable(self):
        """The module declared in pyproject.toml [project.scripts] must be importable."""
        import tomllib

        with PYPROJECT_TOML.open("rb") as f:
            data = tomllib.load(f)

        scripts = data.get("project", {}).get("scripts", {})
        entry = scripts.get("agent-sandbox", "")
        module_path, _, attr = entry.partition(":")

        mod = importlib.import_module(module_path)
        assert mod is not None, f"Could not import module '{module_path}'"

    def test_entry_point_attr_is_callable(self):
        """The attr declared in pyproject.toml [project.scripts] must be callable."""
        import tomllib

        with PYPROJECT_TOML.open("rb") as f:
            data = tomllib.load(f)

        scripts = data.get("project", {}).get("scripts", {})
        entry = scripts.get("agent-sandbox", "")
        module_path, _, attr = entry.partition(":")

        mod = importlib.import_module(module_path)
        fn = getattr(mod, attr, None)
        assert fn is not None, (
            f"Module '{module_path}' has no attribute '{attr}'"
        )
        assert callable(fn), (
            f"'{module_path}.{attr}' is not callable: {type(fn)}"
        )


# ---------------------------------------------------------------------------
# 5. Containerfile ENTRYPOINT — acceptance criterion 3
# ---------------------------------------------------------------------------


class TestContainerfileEntryPoint:
    """Acceptance criterion 3: Containerfile ENTRYPOINT references canonical module."""

    def test_containerfile_exists(self):
        """container/Containerfile must exist."""
        assert CONTAINERFILE.is_file(), (
            f"Containerfile not found at {CONTAINERFILE}"
        )

    def test_containerfile_entrypoint_references_shell_script(self):
        """Containerfile ENTRYPOINT must reference entrypoint.sh.

        The container uses a shell entrypoint so a root init phase can install
        declared apt packages before dropping privileges to the project user.
        """
        content = CONTAINERFILE.read_text(encoding="utf-8")
        assert "entrypoint.sh" in content, (
            "Containerfile does not reference entrypoint.sh. "
            "The ENTRYPOINT must be the shell script that handles the root "
            "package-install phase before dropping to the project user."
        )


# ---------------------------------------------------------------------------
# 6. Cross-file consistency — criterion 3 (parametrized)
# ---------------------------------------------------------------------------


def _extract_module_from_pyproject(content: str) -> str:
    """Extract the agent-sandbox script module path from pyproject.toml text."""
    import tomllib

    data = tomllib.loads(content)
    scripts = data.get("project", {}).get("scripts", {})
    entry = scripts.get("agent-sandbox", "")
    module_path, _, _ = entry.partition(":")
    return module_path


@pytest.mark.parametrize(
    "filepath,extractor",
    [
        (PYPROJECT_TOML, _extract_module_from_pyproject),
    ],
    ids=["pyproject.toml"],
)
def test_canonical_module_path_in_file(filepath, extractor):
    """Criterion 3: pyproject.toml references the canonical module.

    Note: the Containerfile uses a shell script entrypoint (not the Python
    CLI module) so that a root init phase can install declared apt packages
    before dropping privileges to the project user.  The Python module check
    therefore applies only to pyproject.toml.
    """
    content = filepath.read_text(encoding="utf-8")
    module_path = extractor(content)
    assert module_path == CANONICAL_MODULE, (
        f"In {filepath.name}: expected module path '{CANONICAL_MODULE}', "
        f"got '{module_path}'."
    )


def test_containerfile_uses_shell_entrypoint():
    """Containerfile ENTRYPOINT must reference the shell script, not a Python module.

    The container uses entrypoint.sh so a root init phase can install
    project-declared apt packages before dropping privileges to the project user.
    """
    content = CONTAINERFILE.read_text(encoding="utf-8")
    assert "entrypoint.sh" in content, (
        "Containerfile does not reference entrypoint.sh. "
        "The ENTRYPOINT must be the shell script."
    )


# ---------------------------------------------------------------------------
# 7. CLI --help invocation — criterion 4
# ---------------------------------------------------------------------------


class TestCLIHelp:
    """Criterion 4: `agent-sandbox --help` exits 0 and prints usage with --agent."""

    def test_help_exits_zero_via_click_runner(self):
        """`agent_sandbox.cli.main.main --help` via CliRunner exits with 0."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0, (
            f"--help exited {result.exit_code}; output:\n{result.output}"
        )

    def test_help_output_mentions_agent_option(self):
        """`--help` output must mention the --agent option."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--agent" in result.output, (
            f"--help output does not mention --agent:\n{result.output}"
        )

    def test_help_output_has_usage_line(self):
        """`--help` output must include a 'Usage:' line."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "Usage:" in result.output or "usage:" in result.output.lower(), (
            f"--help output missing usage line:\n{result.output}"
        )


# ---------------------------------------------------------------------------
# 8. No ModuleNotFoundError on import (explicit import path check)
# ---------------------------------------------------------------------------


class TestNoModuleNotFoundError:
    """Explicit guard: no ModuleNotFoundError on any import path."""

    def test_no_module_not_found_error_on_cli_main(self):
        """Direct import of canonical module must not raise ModuleNotFoundError."""
        # This test catches the scenario where cli/main.py doesn't exist
        mod = importlib.import_module("agent_sandbox.cli.main")
        assert hasattr(mod, "main"), "agent_sandbox.cli.main.main must exist"

    def test_no_module_not_found_error_on_cli_package(self):
        """Import of cli package must not raise ModuleNotFoundError."""
        mod = importlib.import_module("agent_sandbox.cli")
        assert hasattr(mod, "main"), "agent_sandbox.cli.main must be re-exported"

    def test_canonical_module_file_on_sys_path(self):
        """The canonical module's file must resolve to an existing path."""
        mod = importlib.import_module(CANONICAL_MODULE)
        assert mod.__file__ is not None, (
            f"Module {CANONICAL_MODULE} has no __file__ — is it a namespace package?"
        )
        assert Path(mod.__file__).is_file(), (
            f"Module file does not exist: {mod.__file__}"
        )
