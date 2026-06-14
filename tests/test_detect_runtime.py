"""Tests for FEAT-005: Runtime detection.

TDD: tests written before implementation.

Covers:
  - SubprocessRuntimeAdapter: implements RuntimePort using argument-list subprocess calls
  - DetectRuntimeUseCase: selects runtime from SandboxConfig

Test criteria (from feature spec):
  1. With both runtimes available and runtime=AUTO, Podman is chosen
  2. With only Docker available and runtime=AUTO, Docker is chosen
  3. Explicit runtime=DOCKER/PODMAN is honored even if the other exists
  4. No supported runtime raises SandboxError(RUNTIME_NOT_FOUND) with install guidance
  5. Rootless flags are included in built invocations
  6. Subprocess is invoked with argument lists, never a shell string (verified via mocked runner)
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import pytest

from agent_sandbox.domain.value_objects import RuntimeKind
from agent_sandbox.exceptions import ErrorCode, SandboxError

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

SRC_DIR = Path(__file__).parent.parent / "src"


def _infra_path(filename: str) -> Path:
    return SRC_DIR / "agent_sandbox" / "infrastructure" / filename


def _use_case_path(filename: str) -> Path:
    return SRC_DIR / "agent_sandbox" / "application" / "use_cases" / filename


# ---------------------------------------------------------------------------
# Mock runner factory helpers
# ---------------------------------------------------------------------------

def make_runner(*available_binaries: str) -> Callable:
    """Return a mock subprocess runner that succeeds for given binaries only.

    The runner captures all calls so tests can assert on argument lists.
    Raises FileNotFoundError for binaries not in *available_binaries*, matching
    the real OS behaviour when a binary is not on PATH.
    """
    calls: list[list[str]] = []

    def runner(args: list[str], timeout=None) -> tuple[int, str, str]:
        assert isinstance(args, list), (
            "Runner MUST be called with an argument list, not a shell string"
        )
        calls.append(list(args))
        binary = args[0]
        if binary in available_binaries:
            return (0, f"{binary} version 4.0.0", "")
        raise FileNotFoundError(f"binary not found: {binary!r}")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def make_capturing_runner(*available_binaries: str) -> Callable:
    """Like make_runner but also captures the calls for assertion."""
    return make_runner(*available_binaries)


# ---------------------------------------------------------------------------
# 1. Module / file existence
# ---------------------------------------------------------------------------

class TestModuleFilesExist:
    """Required source files must exist on disk before running other tests."""

    def test_subprocess_runtime_module_exists(self):
        path = _infra_path("subprocess_runtime.py")
        assert path.is_file(), f"Missing: {path}"

    def test_detect_runtime_use_case_module_exists(self):
        path = _use_case_path("detect_runtime.py")
        assert path.is_file(), f"Missing: {path}"


# ---------------------------------------------------------------------------
# 2. SubprocessRuntimeAdapter importability
# ---------------------------------------------------------------------------

class TestSubprocessRuntimeAdapterImportable:
    """SubprocessRuntimeAdapter must be importable from infrastructure layer."""

    def test_importable(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter  # noqa: F401
        assert SubprocessRuntimeAdapter is not None

    def test_instantiable_with_defaults(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        adapter = SubprocessRuntimeAdapter()
        assert adapter is not None

    def test_instantiable_with_preferred_auto(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO)
        assert adapter is not None

    def test_instantiable_with_preferred_docker(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER)
        assert adapter is not None

    def test_instantiable_with_preferred_podman(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.PODMAN)
        assert adapter is not None

    def test_has_detect_method(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        assert hasattr(SubprocessRuntimeAdapter, "detect")
        assert callable(SubprocessRuntimeAdapter.detect)

    def test_has_run_cli_method(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        assert hasattr(SubprocessRuntimeAdapter, "run_cli")
        assert callable(SubprocessRuntimeAdapter.run_cli)

    def test_satisfies_runtime_port_protocol(self):
        """SubprocessRuntimeAdapter must be structurally compatible with RuntimePort."""
        from agent_sandbox.application.ports import RuntimePort
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        adapter = SubprocessRuntimeAdapter(runner=make_runner("podman"))
        assert hasattr(adapter, "detect")
        assert hasattr(adapter, "run_cli")
        assert isinstance(adapter, RuntimePort)


# ---------------------------------------------------------------------------
# 3. AUTO mode: criteria 1 & 2
# ---------------------------------------------------------------------------

class TestAutoModeSelection:
    """runtime=AUTO prefers Podman over Docker (criterion 1 & 2)."""

    def test_auto_both_available_chooses_podman(self):
        """Criterion 1: Both available → Podman wins."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("podman", "docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        result = adapter.detect()
        assert result == RuntimeKind.PODMAN

    def test_auto_only_docker_available_chooses_docker(self):
        """Criterion 2: Only Docker available → Docker is returned."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        result = adapter.detect()
        assert result == RuntimeKind.DOCKER

    def test_auto_only_podman_available_chooses_podman(self):
        """Only Podman available → Podman is returned."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("podman")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        result = adapter.detect()
        assert result == RuntimeKind.PODMAN

    def test_auto_returns_runtime_kind(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        result = adapter.detect()
        assert isinstance(result, RuntimeKind)

    def test_auto_default_preferred_behaves_like_auto(self):
        """Default preferred value should be AUTO (Podman-first)."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("podman", "docker")
        adapter = SubprocessRuntimeAdapter(runner=runner)  # no preferred arg
        result = adapter.detect()
        assert result == RuntimeKind.PODMAN


# ---------------------------------------------------------------------------
# 4. Explicit runtime selection: criterion 3
# ---------------------------------------------------------------------------

class TestExplicitRuntimeSelection:
    """Explicit runtime=DOCKER/PODMAN is honored even if both exist (criterion 3)."""

    def test_explicit_docker_chosen_when_both_available(self):
        """Explicit DOCKER → Docker, even though Podman is also present."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("podman", "docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=runner)
        result = adapter.detect()
        assert result == RuntimeKind.DOCKER

    def test_explicit_podman_chosen_when_both_available(self):
        """Explicit PODMAN → Podman, even though Docker is also present."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("podman", "docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.PODMAN, runner=runner)
        result = adapter.detect()
        assert result == RuntimeKind.PODMAN

    def test_explicit_docker_raises_when_docker_missing(self):
        """Explicit DOCKER raises RUNTIME_NOT_FOUND if Docker is not installed."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("podman")  # only Podman
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=runner)
        with pytest.raises(SandboxError) as exc_info:
            adapter.detect()
        assert exc_info.value.code == ErrorCode.RUNTIME_NOT_FOUND

    def test_explicit_podman_raises_when_podman_missing(self):
        """Explicit PODMAN raises RUNTIME_NOT_FOUND if Podman is not installed."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("docker")  # only Docker
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.PODMAN, runner=runner)
        with pytest.raises(SandboxError) as exc_info:
            adapter.detect()
        assert exc_info.value.code == ErrorCode.RUNTIME_NOT_FOUND


# ---------------------------------------------------------------------------
# 5. No runtime available: criterion 4
# ---------------------------------------------------------------------------

class TestNoRuntimeAvailable:
    """When no runtime is found, SandboxError(RUNTIME_NOT_FOUND) is raised with guidance."""

    def test_auto_no_runtime_raises_sandbox_error(self):
        """Criterion 4: No runtime → SandboxError."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner()  # no binaries available
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        with pytest.raises(SandboxError):
            adapter.detect()

    def test_auto_no_runtime_error_code_is_runtime_not_found(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner()
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        with pytest.raises(SandboxError) as exc_info:
            adapter.detect()
        assert exc_info.value.code == ErrorCode.RUNTIME_NOT_FOUND

    def test_auto_no_runtime_message_contains_install_guidance(self):
        """Error message must be actionable (mention installing Docker or Podman)."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner()
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        with pytest.raises(SandboxError) as exc_info:
            adapter.detect()
        msg = str(exc_info.value).lower()
        # Must guide the user on what to install
        assert "docker" in msg or "podman" in msg
        assert "install" in msg

    def test_explicit_docker_no_runtime_message_contains_install_guidance(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner()
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=runner)
        with pytest.raises(SandboxError) as exc_info:
            adapter.detect()
        msg = str(exc_info.value).lower()
        assert "install" in msg

    def test_no_runtime_does_not_raise_file_not_found_error(self):
        """FileNotFoundError must be wrapped in SandboxError, not re-raised."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner()
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        try:
            adapter.detect()
        except SandboxError:
            pass  # correct
        except FileNotFoundError:
            pytest.fail("FileNotFoundError must be wrapped in SandboxError")


# ---------------------------------------------------------------------------
# 6. Rootless flags: criterion 5
# ---------------------------------------------------------------------------

class TestRootlessFlags:
    """Rootless flags are included in run invocations (criterion 5)."""

    def test_podman_run_includes_rootless_flag(self):
        """Podman run invocations must include rootless flag(s)."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("podman")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.PODMAN, runner=runner)
        adapter.detect()
        adapter.run_cli(["run", "ubuntu:22.04", "echo", "hello"])

        # Find the run invocation (not the version probe)
        run_calls = [c for c in runner.calls if c[0] == "podman" and len(c) > 1 and c[1] == "run"]
        assert len(run_calls) >= 1, "Expected a podman run invocation"
        run_args = run_calls[0]
        # Rootless flag must be present somewhere in the run args
        full_arg_str = " ".join(run_args)
        has_rootless_flag = (
            "--userns=keep-id" in run_args
            or "--userns" in run_args
            or "keep-id" in full_arg_str
        )
        assert has_rootless_flag, (
            f"Expected rootless flag in podman run args, got: {run_args}"
        )

    def test_docker_run_includes_security_flag(self):
        """Docker run invocations must include a security/rootless flag."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=runner)
        adapter.detect()
        adapter.run_cli(["run", "ubuntu:22.04", "echo", "hello"])

        run_calls = [c for c in runner.calls if c[0] == "docker" and len(c) > 1 and c[1] == "run"]
        assert len(run_calls) >= 1, "Expected a docker run invocation"
        run_args = run_calls[0]
        full_arg_str = " ".join(run_args)
        # Docker rootless/security flags
        has_security_flag = (
            "--no-new-privileges" in full_arg_str
            or "--security-opt" in run_args
            or "--userns" in run_args
            or "no-new-privileges" in full_arg_str
        )
        assert has_security_flag, (
            f"Expected security/rootless flag in docker run args, got: {run_args}"
        )

    def test_non_run_commands_not_modified(self):
        """Non-run commands (like inspect, ps) should not have rootless flags injected."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("podman")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.PODMAN, runner=runner)
        adapter.detect()
        adapter.run_cli(["ps"])

        ps_calls = [c for c in runner.calls if c[0] == "podman" and len(c) > 1 and c[1] == "ps"]
        assert len(ps_calls) >= 1
        # ps call should just be ["podman", "ps"] with no extra rootless injection
        ps_args = ps_calls[0]
        assert "--userns=keep-id" not in ps_args


# ---------------------------------------------------------------------------
# 7. Argument list (no shell string): criterion 6
# ---------------------------------------------------------------------------

class TestArgumentListInvocation:
    """Subprocess must be invoked with argument lists, never shell strings (criterion 6)."""

    def test_detect_calls_runner_with_list_for_version_probe(self):
        """detect() must call runner with a list, not a string."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        received_args = []

        def capturing_runner(args, timeout=None):
            received_args.append(args)
            assert isinstance(args, list), (
                f"Runner must receive a list, got {type(args).__name__!r}: {args!r}"
            )
            if args[0] == "podman":
                return (0, "podman version 4.0.0", "")
            raise FileNotFoundError(f"not found: {args[0]}")

        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=capturing_runner)
        adapter.detect()
        assert len(received_args) >= 1
        for call in received_args:
            assert isinstance(call, list), f"Expected list, got: {call!r}"

    def test_run_cli_calls_runner_with_list(self):
        """run_cli() must forward a list to the runner, never a shell string."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        received_args = []

        def capturing_runner(args, timeout=None):
            received_args.append(args)
            assert isinstance(args, list), (
                f"Runner must receive a list, got {type(args).__name__!r}: {args!r}"
            )
            return (0, "ok", "")

        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=capturing_runner)
        adapter.detect()
        adapter.run_cli(["run", "--rm", "ubuntu:22.04", "echo", "hello"])

        # Find the actual run call
        run_calls = [c for c in received_args if len(c) > 0 and c[0] == "docker" and len(c) > 1 and c[1] == "run"]
        assert len(run_calls) >= 1
        for call in run_calls:
            assert isinstance(call, list), f"Expected list, got: {call!r}"

    def test_runner_receives_binary_as_first_element(self):
        """The binary name must be the first element of the argument list."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        received_args = []

        def capturing_runner(args, timeout=None):
            received_args.append(args)
            return (0, "ok", "")

        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=capturing_runner)
        adapter.detect()
        for call in received_args:
            assert call[0] == "docker", (
                f"First element of args list must be binary name 'docker', got: {call[0]!r}"
            )

    def test_version_probe_uses_version_flag(self):
        """detect() probes by running [binary, '--version'] (or equivalent)."""
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        received_args = []

        def capturing_runner(args, timeout=None):
            received_args.append(list(args))
            if args[0] == "podman":
                return (0, "podman version 4.0.0", "")
            raise FileNotFoundError("not found")

        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=capturing_runner)
        adapter.detect()

        # Must have probed podman
        podman_probes = [c for c in received_args if c[0] == "podman"]
        assert len(podman_probes) >= 1


# ---------------------------------------------------------------------------
# 8. run_cli return value
# ---------------------------------------------------------------------------

class TestRunCliReturnValue:
    """run_cli must return (exit_code, stdout, stderr) as a 3-tuple."""

    def test_run_cli_returns_tuple(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=runner)
        adapter.detect()
        result = adapter.run_cli(["ps"])
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_run_cli_exit_code_is_int(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=runner)
        adapter.detect()
        exit_code, stdout, stderr = adapter.run_cli(["ps"])
        assert isinstance(exit_code, int)

    def test_run_cli_stdout_is_str(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=runner)
        adapter.detect()
        exit_code, stdout, stderr = adapter.run_cli(["ps"])
        assert isinstance(stdout, str)

    def test_run_cli_stderr_is_str(self):
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter
        runner = make_runner("docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=runner)
        adapter.detect()
        exit_code, stdout, stderr = adapter.run_cli(["ps"])
        assert isinstance(stderr, str)


# ---------------------------------------------------------------------------
# 9. DetectRuntimeUseCase importability
# ---------------------------------------------------------------------------

class TestDetectRuntimeUseCaseImportable:
    """DetectRuntimeUseCase must be importable from application use_cases layer."""

    def test_importable(self):
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase  # noqa: F401
        assert DetectRuntimeUseCase is not None

    def test_instantiable_with_runtime_port(self):
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase

        class FakePort:
            def detect(self):
                return RuntimeKind.DOCKER
            def run_cli(self, args, timeout=None):
                return (0, "", "")

        use_case = DetectRuntimeUseCase(runtime_port=FakePort())
        assert use_case is not None

    def test_has_execute_method(self):
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        assert hasattr(DetectRuntimeUseCase, "execute")
        assert callable(DetectRuntimeUseCase.execute)


# ---------------------------------------------------------------------------
# 10. DetectRuntimeUseCase behaviour
# ---------------------------------------------------------------------------

class TestDetectRuntimeUseCaseBehaviour:
    """DetectRuntimeUseCase.execute() selects the runtime from config."""

    def _make_fake_port(self, returns: RuntimeKind):
        class FakePort:
            def detect(self_inner):
                return returns
            def run_cli(self_inner, args, timeout=None):
                return (0, "", "")
        return FakePort()

    def test_execute_returns_runtime_kind(self):
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig
        use_case = DetectRuntimeUseCase(runtime_port=self._make_fake_port(RuntimeKind.PODMAN))
        config = SandboxConfig()
        result = use_case.execute(config)
        assert isinstance(result, RuntimeKind)

    def test_execute_with_auto_config_delegates_to_port(self):
        """AUTO config: use case delegates to port.detect()."""
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig
        use_case = DetectRuntimeUseCase(runtime_port=self._make_fake_port(RuntimeKind.PODMAN))
        config = SandboxConfig(runtime=RuntimeKind.AUTO)
        result = use_case.execute(config)
        assert result == RuntimeKind.PODMAN

    def test_execute_with_docker_config_returns_docker_from_port(self):
        """DOCKER config with port returning DOCKER → DOCKER."""
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig
        use_case = DetectRuntimeUseCase(runtime_port=self._make_fake_port(RuntimeKind.DOCKER))
        config = SandboxConfig(runtime=RuntimeKind.DOCKER)
        result = use_case.execute(config)
        assert result == RuntimeKind.DOCKER

    def test_execute_with_podman_config_returns_podman_from_port(self):
        """PODMAN config with port returning PODMAN → PODMAN."""
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig
        use_case = DetectRuntimeUseCase(runtime_port=self._make_fake_port(RuntimeKind.PODMAN))
        config = SandboxConfig(runtime=RuntimeKind.PODMAN)
        result = use_case.execute(config)
        assert result == RuntimeKind.PODMAN

    def test_execute_propagates_sandbox_error_from_port(self):
        """SandboxError from port.detect() must propagate to caller."""
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig

        class ErrorPort:
            def detect(self):
                raise SandboxError(
                    "No runtime found",
                    code=ErrorCode.RUNTIME_NOT_FOUND,
                )
            def run_cli(self, args, timeout=None):
                return (0, "", "")

        use_case = DetectRuntimeUseCase(runtime_port=ErrorPort())
        config = SandboxConfig(runtime=RuntimeKind.AUTO)
        with pytest.raises(SandboxError) as exc_info:
            use_case.execute(config)
        assert exc_info.value.code == ErrorCode.RUNTIME_NOT_FOUND


# ---------------------------------------------------------------------------
# 11. Integration: use case + real adapter (mock runner)
# ---------------------------------------------------------------------------

class TestDetectRuntimeIntegration:
    """End-to-end: DetectRuntimeUseCase + SubprocessRuntimeAdapter with mock runner."""

    def test_integration_auto_both_available_returns_podman(self):
        """Criterion 1 (integration): Both runtimes → Podman chosen."""
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        runner = make_runner("podman", "docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        use_case = DetectRuntimeUseCase(runtime_port=adapter)
        config = SandboxConfig(runtime=RuntimeKind.AUTO)
        result = use_case.execute(config)
        assert result == RuntimeKind.PODMAN

    def test_integration_auto_only_docker_returns_docker(self):
        """Criterion 2 (integration): Only Docker → Docker chosen."""
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        runner = make_runner("docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        use_case = DetectRuntimeUseCase(runtime_port=adapter)
        config = SandboxConfig(runtime=RuntimeKind.AUTO)
        result = use_case.execute(config)
        assert result == RuntimeKind.DOCKER

    def test_integration_explicit_docker_honored(self):
        """Criterion 3 (integration): Explicit DOCKER honored even if Podman available."""
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        runner = make_runner("podman", "docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.DOCKER, runner=runner)
        use_case = DetectRuntimeUseCase(runtime_port=adapter)
        config = SandboxConfig(runtime=RuntimeKind.DOCKER)
        result = use_case.execute(config)
        assert result == RuntimeKind.DOCKER

    def test_integration_explicit_podman_honored(self):
        """Criterion 3 (integration): Explicit PODMAN honored even if Docker available."""
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        runner = make_runner("podman", "docker")
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.PODMAN, runner=runner)
        use_case = DetectRuntimeUseCase(runtime_port=adapter)
        config = SandboxConfig(runtime=RuntimeKind.PODMAN)
        result = use_case.execute(config)
        assert result == RuntimeKind.PODMAN

    def test_integration_no_runtime_raises_sandbox_error(self):
        """Criterion 4 (integration): No runtime → SandboxError propagates."""
        from agent_sandbox.application.use_cases.detect_runtime import DetectRuntimeUseCase
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.infrastructure.subprocess_runtime import SubprocessRuntimeAdapter

        runner = make_runner()
        adapter = SubprocessRuntimeAdapter(preferred=RuntimeKind.AUTO, runner=runner)
        use_case = DetectRuntimeUseCase(runtime_port=adapter)
        config = SandboxConfig(runtime=RuntimeKind.AUTO)
        with pytest.raises(SandboxError) as exc_info:
            use_case.execute(config)
        assert exc_info.value.code == ErrorCode.RUNTIME_NOT_FOUND


# ---------------------------------------------------------------------------
# 12. Import purity: detect_runtime.py (application layer)
# ---------------------------------------------------------------------------

class TestDetectRuntimeUseCaseImportPurity:
    """detect_runtime.py must import only domain + ports + stdlib (application layer).

    No subprocess, click, docker, podman, or infrastructure modules permitted.
    """

    def _get_ast(self) -> ast.Module:
        path = _use_case_path("detect_runtime.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_subprocess_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "subprocess", (
                        "detect_runtime.py (application layer) must not import 'subprocess'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "subprocess", (
                        "detect_runtime.py must not import from 'subprocess'"
                    )

    def test_no_infrastructure_import(self):
        tree = self._get_ast()
        forbidden_infra_modules = {"subprocess", "click", "fastapi", "sqlalchemy",
                                   "flask", "django", "docker", "podman"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden_infra_modules, (
                        f"detect_runtime.py must not import '{top}' (application layer)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden_infra_modules, (
                        f"detect_runtime.py must not import from '{top}'"
                    )

    def test_no_infrastructure_layer_import(self):
        """Application use case must not import from infrastructure layer."""
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "infrastructure" in node.module:
                    pytest.fail(
                        f"detect_runtime.py must not import from infrastructure: {node.module!r}"
                    )


# ---------------------------------------------------------------------------
# 13. Import purity: subprocess_runtime.py (infrastructure layer)
# ---------------------------------------------------------------------------

class TestSubprocessRuntimeImportPurity:
    """subprocess_runtime.py (infrastructure) may use subprocess + domain + stdlib.

    Must not import heavy frameworks (fastapi, sqlalchemy, click, etc.).
    """

    def _get_ast(self) -> ast.Module:
        path = _infra_path("subprocess_runtime.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_may_import_subprocess(self):
        """Infrastructure IS allowed to import subprocess (that's its job)."""
        tree = self._get_ast()
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess":
                        found = True
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module == "subprocess":
                    found = True
        assert found, "subprocess_runtime.py should import subprocess"

    def test_no_framework_imports(self):
        """Infrastructure must not import heavy web/ORM frameworks."""
        forbidden = {"click", "fastapi", "sqlalchemy", "flask", "django"}
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, (
                        f"subprocess_runtime.py must not import '{top}'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"subprocess_runtime.py must not import from '{top}'"
                    )
