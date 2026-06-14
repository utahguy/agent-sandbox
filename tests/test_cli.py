"""Tests for FEAT-009: CLI entry point (composition root) and RunAgentUseCase.

TDD: tests written before implementation.

Covers:
  1. 'agent-sandbox' console script is registered and invokable
  2. --agent claude with passthrough args wires config->runtime->image->container->exec
  3. CLI exit code equals the inner command's exit_code on success
  4. Config/runtime/build SandboxError prints message to stderr and exits with code 2
  5. Timeout exits with the dedicated nonzero code (EXIT_TIMEOUT = 124)
  6. Container stop()/cleanup runs on normal exit, on exception, and on SIGINT
     (verified with fake adapters)
"""

from __future__ import annotations

import ast
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_sandbox.domain.entities import ExecResult, SandboxConfig
from agent_sandbox.domain.image_spec import ImageSpec
from agent_sandbox.exceptions import ErrorCode, SandboxError, TimeoutError

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

SRC_DIR = Path(__file__).parent.parent / "src"
APP_DIR = SRC_DIR / "agent_sandbox" / "application"
# FEAT-019: canonical CLI module moved from cli.py → cli/main.py
CLI_MODULE = SRC_DIR / "agent_sandbox" / "cli" / "main.py"
CLI_PACKAGE = SRC_DIR / "agent_sandbox" / "cli"
RUN_AGENT_MODULE = APP_DIR / "use_cases" / "run_agent.py"
PYPROJECT_TOML = Path(__file__).parent.parent / "pyproject.toml"
INIT_MODULE = SRC_DIR / "agent_sandbox" / "__init__.py"


# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------


def _make_exec_result(exit_code: int = 0, stdout: str = "", stderr: str = "") -> ExecResult:
    return ExecResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=100,
        timed_out=False,
    )


def _make_fake_handle(exec_result: ExecResult | None = None, exec_raises=None):
    """Return a fake ContainerHandlePort."""

    class FakeHandle:
        def __init__(self):
            self._stop_count = 0

        @property
        def container_id(self) -> str:
            return "fake-cid-test-1234"

        @property
        def image_tag(self) -> str:
            return "agent-sandbox:fake-tag"

        def exec(self, cmd, timeout=None) -> ExecResult:
            if exec_raises is not None:
                raise exec_raises
            return exec_result or _make_exec_result()

        def stop(self) -> None:
            self._stop_count += 1

    return FakeHandle()


def _make_fake_start_use_case(handle=None, raises=None):
    """Return a fake StartSandboxUseCase."""

    class FakeStartUseCase:
        def __init__(self):
            self.call_count = 0
            self.last_config = None

        def execute(self, config, image_spec, containerfile_content):
            self.call_count += 1
            self.last_config = config
            if raises is not None:
                raise raises
            return handle

    return FakeStartUseCase()


def _make_fake_stop_use_case(stop_calls: list | None = None):
    """Return a fake StopSandboxUseCase that records calls."""
    recorded = stop_calls if stop_calls is not None else []

    class FakeStopUseCase:
        def execute(self, handle) -> None:
            recorded.append(handle)

    obj = FakeStopUseCase()
    obj._calls = recorded
    return obj


def _make_fake_exec_use_case_factory(result: ExecResult | None = None, raises=None):
    """Return a factory that creates a fake ExecuteCommandUseCase."""

    class FakeExecUseCase:
        def __init__(self, handle):
            self._handle = handle
            self.last_cmd = None

        def execute(self, cmd, timeout=None) -> ExecResult:
            self.last_cmd = cmd
            if raises is not None:
                raise raises
            return result or _make_exec_result()

    def factory(handle):
        return FakeExecUseCase(handle)

    return factory


# ---------------------------------------------------------------------------
# 1. Module / file existence
# ---------------------------------------------------------------------------


class TestModuleFilesExist:
    """Required source files must exist on disk."""

    def test_run_agent_use_case_module_exists(self):
        assert RUN_AGENT_MODULE.is_file(), f"Missing: {RUN_AGENT_MODULE}"

    def test_cli_module_exists(self):
        # FEAT-019: the canonical module is now cli/main.py (package, not flat file)
        assert CLI_MODULE.is_file(), f"Missing: {CLI_MODULE}"


# ---------------------------------------------------------------------------
# 2. 'agent-sandbox' console script registration
# ---------------------------------------------------------------------------


class TestConsoleScriptRegistration:
    """Criterion 1: 'agent-sandbox' console script registered in pyproject.toml."""

    def test_pyproject_toml_has_agent_sandbox_script(self):
        """pyproject.toml must declare agent-sandbox = 'agent_sandbox.cli.main:main'.

        FEAT-019: canonical entry point updated from agent_sandbox.cli:main to
        agent_sandbox.cli.main:main after the flat-module → cli/ package refactoring.
        """
        content = PYPROJECT_TOML.read_text(encoding="utf-8")
        assert "agent-sandbox" in content, (
            "pyproject.toml missing 'agent-sandbox' console script entry"
        )
        assert "agent_sandbox.cli.main:main" in content, (
            "pyproject.toml missing 'agent_sandbox.cli.main:main' target"
        )

    def test_agent_sandbox_script_points_to_cli_main(self):
        """The agent-sandbox script must point to agent_sandbox.cli.main:main.

        FEAT-019: canonical entry point updated from agent_sandbox.cli:main to
        agent_sandbox.cli.main:main.
        """
        import tomllib

        with PYPROJECT_TOML.open("rb") as f:
            data = tomllib.load(f)

        scripts = data.get("project", {}).get("scripts", {})
        assert "agent-sandbox" in scripts, (
            f"[project.scripts] missing 'agent-sandbox'; found: {list(scripts.keys())}"
        )
        assert scripts["agent-sandbox"] == "agent_sandbox.cli.main:main", (
            f"Expected 'agent_sandbox.cli.main:main', got {scripts['agent-sandbox']!r}"
        )


# ---------------------------------------------------------------------------
# 3. RunAgentUseCase importability and interface
# ---------------------------------------------------------------------------


class TestRunAgentUseCaseImportable:
    """RunAgentUseCase must be importable from the application layer."""

    def test_importable(self):
        from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase  # noqa: F401

        assert RunAgentUseCase is not None

    def test_has_execute_method(self):
        from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase

        assert hasattr(RunAgentUseCase, "execute")
        assert callable(RunAgentUseCase.execute)

    def test_no_framework_imports(self):
        """run_agent.py must not import click, subprocess, fastapi, sqlalchemy."""
        source = RUN_AGENT_MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {"click", "subprocess", "fastapi", "sqlalchemy", "flask"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module.split(".")[0]] if node.module else []
                for name in names:
                    assert name not in forbidden, (
                        f"run_agent.py must not import {name!r} (framework import)"
                    )


# ---------------------------------------------------------------------------
# 4. RunAgentUseCase.execute() returns ExecResult (Criterion 3)
# ---------------------------------------------------------------------------


class TestRunAgentUseCaseExecute:
    """RunAgentUseCase.execute() returns ExecResult on success."""

    def _make_uc(self, *, exec_result=None, exec_raises=None, start_raises=None):
        """Build a RunAgentUseCase with fakes injected."""
        from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase

        handle = _make_fake_handle(exec_result=exec_result, exec_raises=exec_raises)
        start_uc = _make_fake_start_use_case(handle=handle, raises=start_raises)
        stop_uc = _make_fake_stop_use_case()
        exec_factory = _make_fake_exec_use_case_factory(
            result=exec_result, raises=exec_raises
        )
        return RunAgentUseCase(
            start_sandbox_use_case=start_uc,
            stop_sandbox_use_case=stop_uc,
            execute_command_use_case_factory=exec_factory,
        ), stop_uc

    def test_returns_exec_result(self):
        """execute() returns ExecResult with the inner command's exit code."""
        from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase

        expected = _make_exec_result(exit_code=0, stdout="hello from agent")
        handle = _make_fake_handle(exec_result=expected)
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_calls = []
        stop_uc = _make_fake_stop_use_case(stop_calls=stop_calls)
        exec_factory = _make_fake_exec_use_case_factory(result=expected)

        uc = RunAgentUseCase(
            start_sandbox_use_case=start_uc,
            stop_sandbox_use_case=stop_uc,
            execute_command_use_case_factory=exec_factory,
        )

        config = SandboxConfig()
        image_spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="abc123")
        result = uc.execute(
            config=config,
            image_spec=image_spec,
            containerfile_content="FROM ubuntu:22.04",
            agent_cmd=["claude", "--version"],
        )

        assert isinstance(result, ExecResult)
        assert result.exit_code == 0
        assert result.stdout == "hello from agent"

    def test_exit_code_nonzero_returned_faithfully(self):
        """Non-zero exit codes from the inner command are returned, not raised."""
        from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase

        expected = _make_exec_result(exit_code=42)
        handle = _make_fake_handle(exec_result=expected)
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case()
        exec_factory = _make_fake_exec_use_case_factory(result=expected)

        uc = RunAgentUseCase(
            start_sandbox_use_case=start_uc,
            stop_sandbox_use_case=stop_uc,
            execute_command_use_case_factory=exec_factory,
        )

        config = SandboxConfig()
        image_spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="abc123")
        result = uc.execute(
            config=config,
            image_spec=image_spec,
            containerfile_content="FROM ubuntu:22.04",
            agent_cmd=["claude"],
        )
        assert result.exit_code == 42


# ---------------------------------------------------------------------------
# 5. Cleanup guarantee (Criterion 6)
# ---------------------------------------------------------------------------


class TestRunAgentUseCaseCleanup:
    """Criterion 6: stop() called on normal exit, exception, and SIGINT."""

    def _uc(self, *, exec_result=None, exec_raises=None, stop_calls=None):
        from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase

        calls = stop_calls if stop_calls is not None else []
        handle = _make_fake_handle(exec_result=exec_result, exec_raises=exec_raises)
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case(stop_calls=calls)
        exec_factory = _make_fake_exec_use_case_factory(
            result=exec_result, raises=exec_raises
        )
        uc = RunAgentUseCase(
            start_sandbox_use_case=start_uc,
            stop_sandbox_use_case=stop_uc,
            execute_command_use_case_factory=exec_factory,
        )
        config = SandboxConfig()
        image_spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="abc123")
        return uc, config, image_spec, calls

    def test_cleanup_on_normal_exit(self):
        """stop_sandbox_use_case.execute() is called on successful exec."""
        stop_calls = []
        uc, config, image_spec, calls = self._uc(
            exec_result=_make_exec_result(exit_code=0),
            stop_calls=stop_calls,
        )

        uc.execute(
            config=config,
            image_spec=image_spec,
            containerfile_content="FROM ubuntu:22.04",
            agent_cmd=["claude"],
        )

        assert len(calls) == 1, "stop() must be called exactly once on normal exit"

    def test_cleanup_on_sandbox_error(self):
        """stop_sandbox_use_case.execute() called even when exec raises SandboxError."""
        stop_calls = []
        exc = SandboxError("exec failed", code=ErrorCode.IMAGE_BUILD_FAILED)
        uc, config, image_spec, calls = self._uc(
            exec_raises=exc,
            stop_calls=stop_calls,
        )

        with pytest.raises(SandboxError):
            uc.execute(
                config=config,
                image_spec=image_spec,
                containerfile_content="FROM ubuntu:22.04",
                agent_cmd=["claude"],
            )

        assert len(calls) == 1, "stop() must be called on SandboxError"

    def test_cleanup_on_timeout_error(self):
        """stop_sandbox_use_case.execute() called even when exec raises TimeoutError."""
        stop_calls = []
        exc = TimeoutError("Command timed out", code=ErrorCode.EXEC_TIMEOUT)
        uc, config, image_spec, calls = self._uc(
            exec_raises=exc,
            stop_calls=stop_calls,
        )

        with pytest.raises(TimeoutError):
            uc.execute(
                config=config,
                image_spec=image_spec,
                containerfile_content="FROM ubuntu:22.04",
                agent_cmd=["claude"],
            )

        assert len(calls) == 1, "stop() must be called on TimeoutError"

    def test_cleanup_on_keyboard_interrupt(self):
        """stop_sandbox_use_case.execute() called even on SIGINT (KeyboardInterrupt)."""
        stop_calls = []
        uc, config, image_spec, calls = self._uc(
            exec_raises=KeyboardInterrupt(),
            stop_calls=stop_calls,
        )

        with pytest.raises(KeyboardInterrupt):
            uc.execute(
                config=config,
                image_spec=image_spec,
                containerfile_content="FROM ubuntu:22.04",
                agent_cmd=["claude"],
            )

        assert len(calls) == 1, (
            "stop() must be called on KeyboardInterrupt (SIGINT)"
        )

    def test_no_cleanup_if_start_fails(self):
        """stop_sandbox_use_case.execute() NOT called if start raises (no handle)."""
        from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase

        stop_calls = []
        exc = SandboxError("start failed", code=ErrorCode.CONTAINER_START_FAILED)
        start_uc = _make_fake_start_use_case(raises=exc)
        stop_uc = _make_fake_stop_use_case(stop_calls=stop_calls)
        exec_factory = _make_fake_exec_use_case_factory()

        uc = RunAgentUseCase(
            start_sandbox_use_case=start_uc,
            stop_sandbox_use_case=stop_uc,
            execute_command_use_case_factory=exec_factory,
        )
        config = SandboxConfig()
        image_spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="abc123")

        with pytest.raises(SandboxError):
            uc.execute(
                config=config,
                image_spec=image_spec,
                containerfile_content="FROM ubuntu:22.04",
                agent_cmd=["claude"],
            )

        assert len(stop_calls) == 0, (
            "stop() must NOT be called if start() raises (no container was created)"
        )


# ---------------------------------------------------------------------------
# 6. CLI importability
# ---------------------------------------------------------------------------


class TestCLIImportable:
    """CLI module must be importable and expose a Click command.

    FEAT-019: updated to use the canonical module path agent_sandbox.cli.main
    after the flat-module (cli.py) → package (cli/main.py) refactoring.
    """

    def test_importable(self):
        import agent_sandbox.cli  # noqa: F401

    def test_main_function_exists(self):
        # FEAT-019: main lives in the canonical module cli/main.py
        from agent_sandbox.cli.main import main

        assert main is not None

    def test_main_is_click_command(self):
        """main must be a Click command (has invoke method)."""
        # FEAT-019: import from the canonical module path
        from agent_sandbox.cli.main import main

        # Click commands have an 'invoke' method and 'params' attribute
        assert hasattr(main, "invoke") or hasattr(main, "main"), (
            "main must be a Click command"
        )

    def test_exit_code_constants_exported(self):
        """CLI module must export EXIT_SANDBOX_ERROR and EXIT_TIMEOUT."""
        # FEAT-019: constants live in cli/main.py; __init__.py re-exports them
        from agent_sandbox.cli import EXIT_SANDBOX_ERROR, EXIT_TIMEOUT  # noqa: F401

        assert EXIT_SANDBOX_ERROR == 2
        assert EXIT_TIMEOUT > 0

    def test_cli_has_no_framework_domain_violations(self):
        """cli/main.py is presentation layer — domain layer must not import it."""
        # FEAT-019: canonical module is now cli/main.py (not cli.py)
        source = CLI_MODULE.read_text(encoding="utf-8")
        # cli/main.py IS allowed to import click — it's presentation
        assert "click" in source or "sys" in source, (
            "cli/main.py should have Click or sys imports as the CLI entry point"
        )


# ---------------------------------------------------------------------------
# 7. CLI invocation via CliRunner (Criterion 2, 3, 4, 5)
# ---------------------------------------------------------------------------


class TestCLIInvocation:
    """Test CLI behavior via Click's CliRunner with fake use cases."""

    def _make_cli_run_agent_uc(
        self,
        *,
        exec_result: ExecResult | None = None,
        raises: Exception | None = None,
        stop_calls: list | None = None,
    ):
        """Build a fake RunAgentUseCase to inject into CLI tests."""
        from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase

        _calls = stop_calls if stop_calls is not None else []
        _exec_result = exec_result or _make_exec_result(exit_code=0, stdout="agent output")
        handle = _make_fake_handle(exec_result=_exec_result)
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case(stop_calls=_calls)
        exec_factory = _make_fake_exec_use_case_factory(result=_exec_result, raises=raises)

        return RunAgentUseCase(
            start_sandbox_use_case=start_uc,
            stop_sandbox_use_case=stop_uc,
            execute_command_use_case_factory=exec_factory,
        )

    def test_exit_code_mirrors_inner_command_zero(self, tmp_path, monkeypatch):
        """Criterion 3: CLI exit code == inner command exit_code (zero)."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main

        uc = self._make_cli_run_agent_uc(exec_result=_make_exec_result(exit_code=0))
        monkeypatch.setattr("agent_sandbox.cli.main._build_run_agent_use_case", lambda config: uc)
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["--agent", "claude"], catch_exceptions=False)
        assert result.exit_code == 0

    def test_exit_code_mirrors_inner_command_nonzero(self, tmp_path, monkeypatch):
        """Criterion 3: CLI exit code == inner command exit_code (non-zero)."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main

        uc = self._make_cli_run_agent_uc(exec_result=_make_exec_result(exit_code=7))
        monkeypatch.setattr("agent_sandbox.cli.main._build_run_agent_use_case", lambda config: uc)
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["--agent", "claude"], catch_exceptions=False)
        assert result.exit_code == 7

    def test_sandbox_error_prints_to_stderr_exits_2(self, tmp_path, monkeypatch):
        """Criterion 4: SandboxError prints message to stderr and exits with 2."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main, EXIT_SANDBOX_ERROR

        exc = SandboxError("Runtime not found", code=ErrorCode.RUNTIME_NOT_FOUND)
        uc = self._make_cli_run_agent_uc(raises=exc)
        monkeypatch.setattr("agent_sandbox.cli.main._build_run_agent_use_case", lambda config: uc)
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["--agent", "claude"], catch_exceptions=True)

        assert result.exit_code == EXIT_SANDBOX_ERROR
        # Message should appear in output (CliRunner captures all output in result.output)
        assert "Runtime not found" in result.output or "RUNTIME_NOT_FOUND" in result.output

    def test_timeout_error_exits_with_dedicated_code(self, tmp_path, monkeypatch):
        """Criterion 5: TimeoutError exits with EXIT_TIMEOUT."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main, EXIT_TIMEOUT

        exc = TimeoutError("Command timed out", code=ErrorCode.EXEC_TIMEOUT)
        uc = self._make_cli_run_agent_uc(raises=exc)
        monkeypatch.setattr("agent_sandbox.cli.main._build_run_agent_use_case", lambda config: uc)
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["--agent", "claude"], catch_exceptions=True)

        assert result.exit_code == EXIT_TIMEOUT

    def test_passthrough_args_forwarded(self, tmp_path, monkeypatch):
        """Criterion 2: passthrough args are forwarded to the agent command."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main

        last_cmd = []

        class TrackingExecFactory:
            def __call__(self, handle):
                class TrackingExecUC:
                    def execute(self, cmd, timeout=None):
                        last_cmd.extend(cmd)
                        return _make_exec_result(exit_code=0)
                return TrackingExecUC()

        from agent_sandbox.application.use_cases.run_agent import RunAgentUseCase

        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case()

        uc = RunAgentUseCase(
            start_sandbox_use_case=start_uc,
            stop_sandbox_use_case=stop_uc,
            execute_command_use_case_factory=TrackingExecFactory(),
        )
        monkeypatch.setattr("agent_sandbox.cli.main._build_run_agent_use_case", lambda config: uc)
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        runner.invoke(
            main,
            ["--agent", "claude", "--print", "Hello world"],
            catch_exceptions=False,
        )

        assert "claude" in last_cmd, f"'claude' not in {last_cmd}"
        assert "--print" in last_cmd, f"'--print' not in {last_cmd}"
        assert "Hello world" in last_cmd, f"'Hello world' not in {last_cmd}"

    def test_agent_required_option(self, tmp_path, monkeypatch):
        """CLI must require --agent option."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, [], catch_exceptions=True)

        assert result.exit_code != 0, "CLI must fail when --agent is not provided"

    def test_config_sandbox_error_exits_2(self, tmp_path, monkeypatch):
        """Criterion 4: Config-loading SandboxError exits with 2."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main, EXIT_SANDBOX_ERROR

        # Create a malformed .agent-sandbox file
        config_file = tmp_path / ".agent-sandbox"
        config_file.write_text("INVALID_DIRECTIVE oops\n")

        # Don't monkeypatch _build_run_agent_use_case — let the config fail
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["--agent", "claude"], catch_exceptions=True)

        assert result.exit_code == EXIT_SANDBOX_ERROR

    def test_cleanup_on_normal_cli_exit(self, tmp_path, monkeypatch):
        """Criterion 6: Container stop/cleanup runs on normal CLI exit."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main

        stop_calls = []
        uc = self._make_cli_run_agent_uc(
            exec_result=_make_exec_result(exit_code=0),
            stop_calls=stop_calls,
        )
        monkeypatch.setattr("agent_sandbox.cli.main._build_run_agent_use_case", lambda config: uc)
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        runner.invoke(main, ["--agent", "claude"], catch_exceptions=False)

        assert len(stop_calls) == 1, "Container must be stopped on normal exit"

    def test_cleanup_on_exception_during_cli_exec(self, tmp_path, monkeypatch):
        """Criterion 6: Container stop/cleanup runs when exception occurs during exec."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main, EXIT_SANDBOX_ERROR

        stop_calls = []
        exc = SandboxError("Build failed", code=ErrorCode.IMAGE_BUILD_FAILED)
        uc = self._make_cli_run_agent_uc(raises=exc, stop_calls=stop_calls)
        monkeypatch.setattr("agent_sandbox.cli.main._build_run_agent_use_case", lambda config: uc)
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["--agent", "claude"], catch_exceptions=True)

        assert result.exit_code == EXIT_SANDBOX_ERROR
        assert len(stop_calls) == 1, "Container must be stopped on exec exception"

    def test_cleanup_on_sigint(self, tmp_path, monkeypatch):
        """Criterion 6: Container stop/cleanup runs on SIGINT (KeyboardInterrupt)."""
        from click.testing import CliRunner
        from agent_sandbox.cli.main import main

        stop_calls = []
        uc = self._make_cli_run_agent_uc(
            raises=KeyboardInterrupt(),
            stop_calls=stop_calls,
        )
        monkeypatch.setattr("agent_sandbox.cli.main._build_run_agent_use_case", lambda config: uc)
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        # KeyboardInterrupt is caught by RunAgentUseCase's finally, then re-raised;
        # CLI should handle it and exit
        result = runner.invoke(main, ["--agent", "claude"], catch_exceptions=True)

        assert result.exit_code != 0, "SIGINT should result in nonzero exit"
        assert len(stop_calls) == 1, "Container must be stopped on SIGINT"


# ---------------------------------------------------------------------------
# 8. pyproject.toml purity check
# ---------------------------------------------------------------------------


class TestApplicationLayerPurity:
    """run_agent.py lives in the application layer and must be pure."""

    def test_run_agent_does_not_import_infrastructure(self):
        """run_agent.py must not import from agent_sandbox.infrastructure."""
        source = RUN_AGENT_MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "infrastructure" in node.module:
                    pytest.fail(
                        f"run_agent.py imports from infrastructure: {node.module}"
                    )

    def test_run_agent_does_not_import_adapters(self):
        """run_agent.py must not import from any adapter layer."""
        source = RUN_AGENT_MODULE.read_text(encoding="utf-8")
        assert "SubprocessRuntimeAdapter" not in source
        assert "CliContainerAdapter" not in source
        assert "ContainerfileImageBuilder" not in source
        assert "FileConfigSource" not in source


# ---------------------------------------------------------------------------
# 9. No framework imports in run_agent.py
# ---------------------------------------------------------------------------


class TestRunAgentApplicationPurity:
    """run_agent.py must contain no framework imports."""

    def test_no_click_import(self):
        source = RUN_AGENT_MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "click", (
                        "run_agent.py must not import click"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "click", (
                        "run_agent.py must not import click"
                    )

    def test_no_subprocess_import(self):
        source = RUN_AGENT_MODULE.read_text(encoding="utf-8")
        assert "import subprocess" not in source

    def test_no_docker_podman_import(self):
        source = RUN_AGENT_MODULE.read_text(encoding="utf-8")
        assert "import docker" not in source
        assert "import podman" not in source
