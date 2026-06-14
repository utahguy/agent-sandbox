"""Tests for the agent_sandbox exception hierarchy (ADR-005).

TDD: these tests are written before the implementation.
"""
import ast
import os
import pytest


class TestExceptionHierarchy:
    """Test the SandboxError / TimeoutError class relationships."""

    def test_sandbox_error_is_base_exception(self):
        from agent_sandbox.exceptions import SandboxError
        assert issubclass(SandboxError, Exception)

    def test_timeout_error_is_subclass_of_sandbox_error(self):
        from agent_sandbox.exceptions import SandboxError, TimeoutError
        assert issubclass(TimeoutError, SandboxError)

    def test_sandbox_error_has_human_readable_message(self):
        from agent_sandbox.exceptions import SandboxError
        err = SandboxError("runtime not found", code="RUNTIME_NOT_FOUND")
        assert err.message == "runtime not found"

    def test_sandbox_error_has_machine_code_attribute(self):
        from agent_sandbox.exceptions import SandboxError
        err = SandboxError("runtime not found", code="RUNTIME_NOT_FOUND")
        assert err.code == "RUNTIME_NOT_FOUND"

    def test_timeout_error_has_message_and_code(self):
        from agent_sandbox.exceptions import TimeoutError
        err = TimeoutError("command timed out", code="EXEC_TIMEOUT")
        assert err.message == "command timed out"
        assert err.code == "EXEC_TIMEOUT"

    def test_sandbox_error_is_catchable_as_exception(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(Exception):
            raise SandboxError("something failed", code="CONFIG_MALFORMED")

    def test_timeout_error_is_catchable_as_sandbox_error(self):
        from agent_sandbox.exceptions import SandboxError, TimeoutError
        with pytest.raises(SandboxError):
            raise TimeoutError("timed out", code="EXEC_TIMEOUT")


class TestErrorCodes:
    """All machine-readable error codes from ADR-005 must exist."""

    def test_runtime_not_found_code(self):
        from agent_sandbox.exceptions import ErrorCode
        assert ErrorCode.RUNTIME_NOT_FOUND == "RUNTIME_NOT_FOUND"

    def test_config_malformed_code(self):
        from agent_sandbox.exceptions import ErrorCode
        assert ErrorCode.CONFIG_MALFORMED == "CONFIG_MALFORMED"

    def test_image_build_failed_code(self):
        from agent_sandbox.exceptions import ErrorCode
        assert ErrorCode.IMAGE_BUILD_FAILED == "IMAGE_BUILD_FAILED"

    def test_container_start_failed_code(self):
        from agent_sandbox.exceptions import ErrorCode
        assert ErrorCode.CONTAINER_START_FAILED == "CONTAINER_START_FAILED"

    def test_exec_timeout_code(self):
        from agent_sandbox.exceptions import ErrorCode
        assert ErrorCode.EXEC_TIMEOUT == "EXEC_TIMEOUT"


class TestNoFrameworkImports:
    """exceptions.py must be framework-free (domain layer constraint)."""

    def _get_exceptions_module_path(self) -> str:
        src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
        return os.path.join(src_dir, "agent_sandbox", "exceptions.py")

    def test_exceptions_file_exists(self):
        path = self._get_exceptions_module_path()
        assert os.path.isfile(path), f"exceptions.py not found at {path}"

    def test_no_subprocess_import(self):
        path = self._get_exceptions_module_path()
        with open(path) as f:
            tree = ast.parse(f.read())
        forbidden = {"subprocess", "click", "fastapi", "sqlalchemy", "flask", "django"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, (
                        f"exceptions.py must not import '{top}' (domain layer)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"exceptions.py must not import from '{top}' (domain layer)"
                    )
