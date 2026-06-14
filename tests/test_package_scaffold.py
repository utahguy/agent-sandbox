"""Tests for the agent_sandbox package scaffold (US-001).

TDD: these tests are written before the implementation.
"""
import importlib
import sys
import os
import subprocess
import pytest


class TestPublicAPIImports:
    """The package must export Sandbox, SandboxConfig, SandboxError, TimeoutError."""

    def test_sandbox_is_importable_from_package_root(self):
        from agent_sandbox import Sandbox
        assert Sandbox is not None

    def test_sandbox_config_is_importable_from_package_root(self):
        from agent_sandbox import SandboxConfig
        assert SandboxConfig is not None

    def test_sandbox_error_is_importable_from_package_root(self):
        from agent_sandbox import SandboxError
        assert SandboxError is not None

    def test_timeout_error_is_importable_from_package_root(self):
        from agent_sandbox import TimeoutError
        assert TimeoutError is not None

    def test_sandbox_and_sandbox_config_import_together(self):
        from agent_sandbox import Sandbox, SandboxConfig
        assert Sandbox is not None
        assert SandboxConfig is not None

    def test_package_has_version(self):
        import agent_sandbox
        assert hasattr(agent_sandbox, "__version__")
        assert isinstance(agent_sandbox.__version__, str)
        assert len(agent_sandbox.__version__) > 0


class TestPackageMetadata:
    """pyproject.toml must declare the package correctly."""

    def test_pyproject_toml_exists(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        pyproject = os.path.join(root, "pyproject.toml")
        assert os.path.isfile(pyproject), "pyproject.toml must exist at project root"

    def test_package_can_be_found_by_python(self):
        """Package should be importable (editable install or src on path)."""
        import agent_sandbox
        # Verify it has a __file__ attribute pointing to our src directory
        assert agent_sandbox.__file__ is not None

    def test_src_layout_structure_exists(self):
        """src/agent_sandbox/ directory must exist."""
        root = os.path.join(os.path.dirname(__file__), "..")
        src_pkg = os.path.join(root, "src", "agent_sandbox")
        assert os.path.isdir(src_pkg), "src/agent_sandbox/ must exist"

    def test_init_file_exists(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        init_file = os.path.join(root, "src", "agent_sandbox", "__init__.py")
        assert os.path.isfile(init_file), "src/agent_sandbox/__init__.py must exist"

    def test_exceptions_file_exists(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        exc_file = os.path.join(root, "src", "agent_sandbox", "exceptions.py")
        assert os.path.isfile(exc_file), "src/agent_sandbox/exceptions.py must exist"


class TestPlaceholderClasses:
    """Sandbox and SandboxConfig are placeholders at this stage."""

    def test_sandbox_is_a_class(self):
        from agent_sandbox import Sandbox
        assert isinstance(Sandbox, type)

    def test_sandbox_config_is_a_class(self):
        from agent_sandbox import SandboxConfig
        assert isinstance(SandboxConfig, type)

    def test_sandbox_can_be_instantiated(self):
        from agent_sandbox import Sandbox
        # Placeholder must be instantiatable (no required args yet)
        obj = Sandbox()
        assert obj is not None

    def test_sandbox_config_can_be_instantiated(self):
        from agent_sandbox import SandboxConfig
        # Placeholder must be instantiatable (no required args yet)
        obj = SandboxConfig()
        assert obj is not None
