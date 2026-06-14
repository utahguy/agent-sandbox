"""Tests for FEAT-008: Command execution inside the running container.

TDD: tests written before implementation.

Covers:
  - ExecuteCommandUseCase: application-layer use case for exec
  - CliContainerHandle.exec() timeout handling
  - ExecResult: separate streams, duration_ms, exit_code, timed_out

Test criteria (from feature spec):
  1. exec returns ExecResult with exit_code, stdout, stderr captured separately
  2. A command exiting non-zero returns ExecResult without raising
  3. A command exceeding timeout raises TimeoutError with EXEC_TIMEOUT code
     and timed_out=True
  4. duration_ms is populated
  5. exec passes cmd as an argument list (no shell injection from cmd/args)
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from agent_sandbox.domain.entities import ExecResult, SandboxConfig
from agent_sandbox.domain.value_objects import RuntimeKind
from agent_sandbox.exceptions import ErrorCode, SandboxError

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

SRC_DIR = Path(__file__).parent.parent / "src"
APP_DIR = SRC_DIR / "agent_sandbox" / "application"
INFRA_DIR = SRC_DIR / "agent_sandbox" / "infrastructure"


def _use_case_path(filename: str) -> Path:
    return APP_DIR / "use_cases" / filename


def _infra_path(filename: str) -> Path:
    return INFRA_DIR / filename


# ---------------------------------------------------------------------------
# Fake / stub helpers
# ---------------------------------------------------------------------------


def _make_fake_runtime(
    *,
    exec_exit: int = 0,
    exec_stdout: str = "output",
    exec_stderr: str = "",
    raise_timeout: bool = False,
):
    """Fake RuntimePort that records calls and optionally raises on exec."""

    recorded_calls: list[list[str]] = []

    def _runner(args: list[str], timeout=None) -> tuple[int, str, str]:
        assert isinstance(args, list), (
            f"run_cli must receive a list, got {type(args).__name__!r}: {args!r}"
        )
        recorded_calls.append(list(args))
        sub = next((a for a in args if not a.startswith("-")), "")
        if sub == "exec":
            if raise_timeout:
                # Simulate subprocess timing out
                raise subprocess.TimeoutExpired(cmd=args, timeout=timeout or 0)
            return (exec_exit, exec_stdout, exec_stderr)
        elif sub in ("stop", "rm"):
            return (0, "", "")
        else:
            return (0, "fake-version", "")

    class FakeRuntime:
        def __init__(self):
            self.calls = recorded_calls

        def detect(self) -> RuntimeKind:
            return RuntimeKind.DOCKER

        def run_cli(self, args: list[str], timeout=None) -> tuple[int, str, str]:
            return _runner(args, timeout=timeout)

    return FakeRuntime()


def _make_fake_handle(
    *,
    exec_exit: int = 0,
    exec_stdout: str = "stdout output",
    exec_stderr: str = "stderr output",
    raise_timeout: bool = False,
    duration_ms: int = 10,
):
    """Create a CliContainerHandle with a configured fake runtime."""
    from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

    fake_runtime = _make_fake_runtime(
        exec_exit=exec_exit,
        exec_stdout=exec_stdout,
        exec_stderr=exec_stderr,
        raise_timeout=raise_timeout,
    )
    return CliContainerHandle(
        container_id="test-container-123",
        image_tag="agent-sandbox:test",
        runtime_port=fake_runtime,
    )


# ---------------------------------------------------------------------------
# 1. Module/file existence
# ---------------------------------------------------------------------------


class TestModuleFilesExist:
    """Required source files must exist on disk."""

    def test_execute_command_use_case_module_exists(self):
        path = _use_case_path("execute_command.py")
        assert path.is_file(), f"Missing: {path}"


# ---------------------------------------------------------------------------
# 2. ExecuteCommandUseCase importability
# ---------------------------------------------------------------------------


class TestExecuteCommandUseCaseImportable:
    """ExecuteCommandUseCase must be importable from application use_cases layer."""

    def test_importable(self):
        from agent_sandbox.application.use_cases.execute_command import (  # noqa: F401
            ExecuteCommandUseCase,
        )

        assert ExecuteCommandUseCase is not None

    def test_instantiable_with_handle(self):
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle()
        uc = ExecuteCommandUseCase(container_handle=handle)
        assert uc is not None

    def test_has_execute_method(self):
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        assert hasattr(ExecuteCommandUseCase, "execute")
        assert callable(ExecuteCommandUseCase.execute)


# ---------------------------------------------------------------------------
# 3. Criterion 1 — exec returns ExecResult with separate streams
# ---------------------------------------------------------------------------


class TestExecReturnsExecResult:
    """Criterion 1: exec must return ExecResult with separate stdout/stderr."""

    def test_execute_returns_exec_result(self):
        """ExecuteCommandUseCase.execute() must return ExecResult."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_stdout="hello\n", exec_stderr="")
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["echo", "hello"])
        assert isinstance(result, ExecResult)

    def test_execute_captures_stdout(self):
        """Criterion 1: stdout is captured in ExecResult.stdout."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_stdout="standard output line\n", exec_stderr="")
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["echo", "standard output line"])
        assert "standard output line" in result.stdout

    def test_execute_captures_stderr(self):
        """Criterion 1: stderr is captured separately in ExecResult.stderr."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_stdout="", exec_stderr="error message\n")
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["some-command"])
        assert "error message" in result.stderr

    def test_execute_stdout_and_stderr_are_separate(self):
        """Criterion 1: stdout and stderr are captured as separate strings."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_stdout="OUT", exec_stderr="ERR")
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["mixed-output"])

        # They must be separate — not merged into one stream
        assert result.stdout == "OUT"
        assert result.stderr == "ERR"

    def test_exec_result_has_exit_code(self):
        """ExecResult must include exit_code."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_exit=0)
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["true"])
        assert hasattr(result, "exit_code")
        assert result.exit_code == 0

    def test_exec_result_has_stdout(self):
        """ExecResult must include stdout."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_stdout="some output")
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["cmd"])
        assert hasattr(result, "stdout")

    def test_exec_result_has_stderr(self):
        """ExecResult must include stderr."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_stderr="some error")
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["cmd"])
        assert hasattr(result, "stderr")


# ---------------------------------------------------------------------------
# 4. Criterion 2 — non-zero exit returns ExecResult without raising
# ---------------------------------------------------------------------------


class TestNonZeroExitReturnsResult:
    """Criterion 2: non-zero exit code must NOT raise — returned in ExecResult."""

    def test_non_zero_exit_does_not_raise(self):
        """A command exiting 1 must return ExecResult, not raise."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_exit=1)
        uc = ExecuteCommandUseCase(container_handle=handle)
        # Must NOT raise
        result = uc.execute(["false"])
        assert result is not None

    def test_non_zero_exit_in_result_exit_code(self):
        """Non-zero exit code must appear in ExecResult.exit_code."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_exit=42)
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["exit-42"])
        assert result.exit_code == 42

    def test_exit_code_1_not_raised(self):
        """Exit code 1 (common failure) must not raise."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_exit=1)
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["failing-cmd"])
        assert result.exit_code == 1

    def test_exit_code_127_not_raised(self):
        """Exit code 127 (command not found) must not raise."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_exit=127)
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["nonexistent-cmd"])
        assert result.exit_code == 127

    def test_adapter_non_zero_exit_not_raised(self):
        """CliContainerHandle.exec() itself must not raise on non-zero exit."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime(exec_exit=99, exec_stdout="", exec_stderr="error")
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test-tag",
            runtime_port=fake_rt,
        )
        result = handle.exec(["cmd"])
        assert result.exit_code == 99


# ---------------------------------------------------------------------------
# 5. Criterion 3 — timeout raises TimeoutError with EXEC_TIMEOUT + timed_out=True
# ---------------------------------------------------------------------------


class TestTimeoutRaisesTimeoutError:
    """Criterion 3: a command exceeding timeout raises TimeoutError(EXEC_TIMEOUT)."""

    def test_timeout_raises_sandbox_timeout_error(self):
        """exec() must raise agent_sandbox.exceptions.TimeoutError on timeout."""
        from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime(raise_timeout=True)
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test-tag",
            runtime_port=fake_rt,
        )
        with pytest.raises(SandboxTimeoutError):
            handle.exec(["sleep", "10"], timeout=0.001)

    def test_timeout_error_has_exec_timeout_code(self):
        """TimeoutError raised on exec timeout must have code=EXEC_TIMEOUT."""
        from agent_sandbox.exceptions import ErrorCode, TimeoutError as SandboxTimeoutError
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime(raise_timeout=True)
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test-tag",
            runtime_port=fake_rt,
        )
        with pytest.raises(SandboxTimeoutError) as exc_info:
            handle.exec(["sleep", "10"], timeout=0.001)

        assert exc_info.value.code == ErrorCode.EXEC_TIMEOUT

    def test_timeout_error_has_timed_out_true(self):
        """TimeoutError must have timed_out=True attribute."""
        from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime(raise_timeout=True)
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test-tag",
            runtime_port=fake_rt,
        )
        with pytest.raises(SandboxTimeoutError) as exc_info:
            handle.exec(["sleep", "10"], timeout=0.001)

        assert hasattr(exc_info.value, "timed_out")
        assert exc_info.value.timed_out is True

    def test_timeout_error_is_subclass_of_sandbox_error(self):
        """TimeoutError must be catchable as SandboxError."""
        from agent_sandbox.exceptions import SandboxError, TimeoutError as SandboxTimeoutError
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime(raise_timeout=True)
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test-tag",
            runtime_port=fake_rt,
        )
        with pytest.raises(SandboxError):
            handle.exec(["sleep", "10"], timeout=0.001)

    def test_use_case_propagates_timeout_error(self):
        """ExecuteCommandUseCase.execute() must propagate TimeoutError."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase
        from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError

        handle = _make_fake_handle(raise_timeout=True)
        uc = ExecuteCommandUseCase(container_handle=handle)
        with pytest.raises(SandboxTimeoutError):
            uc.execute(["sleep", "10"], timeout=0.001)

    def test_use_case_timeout_error_has_exec_timeout_code(self):
        """TimeoutError propagated by use case must carry EXEC_TIMEOUT code."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase
        from agent_sandbox.exceptions import ErrorCode, TimeoutError as SandboxTimeoutError

        handle = _make_fake_handle(raise_timeout=True)
        uc = ExecuteCommandUseCase(container_handle=handle)
        with pytest.raises(SandboxTimeoutError) as exc_info:
            uc.execute(["sleep", "10"], timeout=0.001)

        assert exc_info.value.code == ErrorCode.EXEC_TIMEOUT

    def test_timeout_not_confused_with_non_zero_exit(self):
        """TimeoutError must NOT be raised for non-zero exits (only real timeouts)."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase
        from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError

        handle = _make_fake_handle(exec_exit=1, raise_timeout=False)
        uc = ExecuteCommandUseCase(container_handle=handle)
        # Non-zero exit must NOT raise TimeoutError
        result = uc.execute(["failing-cmd"])
        assert not isinstance(result, SandboxTimeoutError)
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 6. Criterion 4 — duration_ms is populated
# ---------------------------------------------------------------------------


class TestDurationMsPopulated:
    """Criterion 4: ExecResult.duration_ms must be populated (>= 0)."""

    def test_exec_result_has_duration_ms(self):
        """ExecResult must have duration_ms attribute."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle()
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["echo", "test"])
        assert hasattr(result, "duration_ms")

    def test_duration_ms_is_integer(self):
        """duration_ms must be an integer."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle()
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["echo", "test"])
        assert isinstance(result.duration_ms, int)

    def test_duration_ms_is_non_negative(self):
        """duration_ms must be >= 0."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle()
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["echo", "test"])
        assert result.duration_ms >= 0

    def test_adapter_duration_ms_non_negative(self):
        """CliContainerHandle.exec() must produce non-negative duration_ms."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime()
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test-tag",
            runtime_port=fake_rt,
        )
        result = handle.exec(["echo", "hi"])
        assert isinstance(result.duration_ms, int)
        assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# 7. Criterion 5 — cmd passed as argument list (no shell injection)
# ---------------------------------------------------------------------------


class TestCmdPassedAsArgumentList:
    """Criterion 5: exec passes cmd as argument list — no shell injection."""

    def test_list_cmd_passed_as_list_to_runtime(self):
        """A list cmd must be forwarded as a list to run_cli."""
        received_as_str: list[str] = []

        class StrictRuntime:
            def detect(self):
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args, timeout=None) -> tuple[int, str, str]:
                if isinstance(args, str):
                    received_as_str.append(args)
                assert isinstance(args, list), (
                    f"run_cli must receive list, got {type(args).__name__!r}"
                )
                return (0, "output", "")

        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        handle = CliContainerHandle(
            container_id="cid",
            image_tag="tag",
            runtime_port=StrictRuntime(),
        )
        handle.exec(["ls", "-la", "/workspace"])
        assert len(received_as_str) == 0, (
            f"exec passed shell strings: {received_as_str}"
        )

    def test_string_cmd_converted_to_list_not_shell(self):
        """String cmd split on whitespace — NOT passed as a shell command."""
        received_calls: list[list[str]] = []

        class RecordingRuntime:
            def detect(self):
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args, timeout=None) -> tuple[int, str, str]:
                assert isinstance(args, list), "Must be a list"
                received_calls.append(list(args))
                return (0, "", "")

        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        handle = CliContainerHandle(
            container_id="cid",
            image_tag="tag",
            runtime_port=RecordingRuntime(),
        )
        handle.exec("echo hello world")

        assert len(received_calls) >= 1
        exec_calls = [c for c in received_calls if "exec" in c]
        assert len(exec_calls) >= 1
        # The string "echo hello world" should be split into ["echo", "hello", "world"]
        exec_cmd_tokens = exec_calls[0]
        assert "echo" in exec_cmd_tokens
        assert "hello" in exec_cmd_tokens
        assert "world" in exec_cmd_tokens

    def test_cmd_tokens_are_positional_args_not_shell_string(self):
        """exec must build positional args: [exec, cid, *cmd_tokens] — no shell=True."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        calls: list[list[str]] = []

        class CaptureRuntime:
            def detect(self):
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args, timeout=None) -> tuple[int, str, str]:
                calls.append(list(args))
                return (0, "", "")

        handle = CliContainerHandle(
            container_id="my-container",
            image_tag="tag",
            runtime_port=CaptureRuntime(),
        )
        handle.exec(["python3", "-c", "print('hello')"])

        exec_calls = [c for c in calls if "exec" in c]
        assert len(exec_calls) >= 1
        all_args = exec_calls[0]
        # Must have exec + container-id + cmd tokens as separate list items
        assert "exec" in all_args
        assert "my-container" in all_args
        assert "python3" in all_args
        assert "-c" in all_args
        assert "print('hello')" in all_args

    def test_use_case_exec_with_list_cmd(self):
        """ExecuteCommandUseCase.execute() with list cmd returns ExecResult."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_stdout="result")
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute(["python3", "--version"])
        assert isinstance(result, ExecResult)

    def test_use_case_exec_with_string_cmd(self):
        """ExecuteCommandUseCase.execute() with string cmd returns ExecResult."""
        from agent_sandbox.application.use_cases.execute_command import ExecuteCommandUseCase

        handle = _make_fake_handle(exec_stdout="result")
        uc = ExecuteCommandUseCase(container_handle=handle)
        result = uc.execute("python3 --version")
        assert isinstance(result, ExecResult)


# ---------------------------------------------------------------------------
# 8. Import purity — execute_command.py must not import infrastructure
# ---------------------------------------------------------------------------


class TestExecuteCommandImportPurity:
    """execute_command.py (application layer) must not import infrastructure."""

    def _get_ast(self) -> ast.Module:
        path = _use_case_path("execute_command.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_subprocess_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "subprocess", (
                        "execute_command.py must not import 'subprocess'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "subprocess", (
                        "execute_command.py must not import from 'subprocess'"
                    )

    def test_no_infrastructure_layer_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "infrastructure" in node.module:
                    pytest.fail(
                        f"execute_command.py must not import from infrastructure: "
                        f"{node.module!r}"
                    )

    def test_no_framework_imports(self):
        forbidden = {"click", "fastapi", "sqlalchemy", "flask", "django", "docker", "podman"}
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden


# ---------------------------------------------------------------------------
# 9. CliContainerHandle.exec() timeout: specific adapter-level checks
# ---------------------------------------------------------------------------


class TestCliContainerHandleExecTimeout:
    """Container adapter must handle timeout via subprocess.TimeoutExpired."""

    def test_adapter_converts_subprocess_timeout_to_sandbox_timeout(self):
        """subprocess.TimeoutExpired from runtime must become agent_sandbox.TimeoutError."""
        from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime(raise_timeout=True)
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test",
            runtime_port=fake_rt,
        )
        with pytest.raises(SandboxTimeoutError):
            handle.exec(["sleep", "100"], timeout=0.001)

    def test_adapter_timeout_error_not_builtin_timeout(self):
        """The raised exception must be agent_sandbox.TimeoutError, not builtin TimeoutError."""
        from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime(raise_timeout=True)
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test",
            runtime_port=fake_rt,
        )
        with pytest.raises(SandboxTimeoutError) as exc_info:
            handle.exec(["sleep", "100"], timeout=0.001)

        # Verify it's our custom type (not Python's builtin TimeoutError)
        assert type(exc_info.value).__module__.startswith("agent_sandbox"), (
            f"Expected agent_sandbox TimeoutError, got: {type(exc_info.value)}"
        )

    def test_non_timeout_exec_does_not_raise_timeout_error(self):
        """Normal exec (no timeout) must not raise TimeoutError."""
        from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime(exec_exit=0, raise_timeout=False)
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test",
            runtime_port=fake_rt,
        )
        # Must not raise
        result = handle.exec(["echo", "hello"])
        assert isinstance(result, ExecResult)

    def test_timeout_message_includes_context(self):
        """TimeoutError message must be descriptive (not empty)."""
        from agent_sandbox.exceptions import TimeoutError as SandboxTimeoutError
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_rt = _make_fake_runtime(raise_timeout=True)
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="test",
            runtime_port=fake_rt,
        )
        with pytest.raises(SandboxTimeoutError) as exc_info:
            handle.exec(["sleep", "100"], timeout=1.5)

        assert len(str(exc_info.value)) > 10, (
            f"TimeoutError message should be descriptive: {str(exc_info.value)!r}"
        )
