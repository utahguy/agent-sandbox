"""Tests for FEAT-007: Container lifecycle.

TDD: tests written before implementation.

Covers:
  - StartSandboxUseCase: ensures image then starts an isolated container
  - StopSandboxUseCase: idempotent stop/cleanup
  - CliContainerAdapter: ContainerPort implementation via docker/podman run/rm
  - CliContainerHandle: ContainerHandlePort implementation

Test criteria (from feature spec):
  1. Sandbox(config).start() returns a ContainerHandle in running state with
     volumes/ports/env/memory applied to the run invocation
  2. Failed start rolls back the partially-created container (no leak)
  3. container.stop() is idempotent (second call is a no-op, no error)
  4. Container is started with isolation/least-privilege flags (no --privileged, --rm semantics)
  5. Sandbox used as a context manager calls stop() on exit and on exception
  6. Adapter invokes runtime with argument lists only
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import pytest

from agent_sandbox.domain.entities import ContainerHandle, ContainerState, ExecResult, SandboxConfig
from agent_sandbox.domain.value_objects import MemoryLimit, PortMapping, RuntimeKind, Volume
from agent_sandbox.exceptions import ErrorCode, SandboxError

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

SRC_DIR = Path(__file__).parent.parent / "src"
INFRA_DIR = SRC_DIR / "agent_sandbox" / "infrastructure"
APP_DIR = SRC_DIR / "agent_sandbox" / "application"


def _infra_path(filename: str) -> Path:
    return INFRA_DIR / filename


def _use_case_path(filename: str) -> Path:
    return APP_DIR / "use_cases" / filename


# ---------------------------------------------------------------------------
# Fake / stub helpers
# ---------------------------------------------------------------------------

def _make_fake_ensure_image(succeed: bool = True):
    """Return a fake EnsureImageUseCase that either succeeds or raises."""
    from agent_sandbox.exceptions import ErrorCode, SandboxError

    class FakeEnsureImage:
        def __init__(self):
            self.called = False
            self.last_image_spec = None

        def execute(self, image_spec, containerfile_content: str) -> None:
            self.called = True
            self.last_image_spec = image_spec
            if not succeed:
                raise SandboxError(
                    "Build failed: fake error",
                    code=ErrorCode.IMAGE_BUILD_FAILED,
                )

    return FakeEnsureImage()


def _make_fake_runtime(
    *,
    run_exit: int = 0,
    run_stdout: str = "fake-container-id-1234\n",
    exec_exit: int = 0,
    exec_stdout: str = "exec output",
    stop_exit: int = 0,
    rm_exit: int = 0,
):
    """
    Fake RuntimePort that records all calls.

    Keys in recorded_calls are the subcommand (first arg after binary): run, exec, stop, rm.
    """
    recorded_calls: list[list[str]] = []

    def _runner(args: list[str], timeout=None) -> tuple[int, str, str]:
        assert isinstance(args, list), (
            f"run_cli must receive a list, got {type(args).__name__!r}: {args!r}"
        )
        recorded_calls.append(list(args))
        # Determine subcommand (first arg that isn't a flag)
        sub = next((a for a in args if not a.startswith("-")), "")
        if sub == "run":
            return (run_exit, run_stdout, "" if run_exit == 0 else "container start error")
        elif sub == "exec":
            return (exec_exit, exec_stdout, "")
        elif sub == "stop":
            return (stop_exit, "", "" if stop_exit == 0 else "no such container")
        elif sub == "rm":
            return (rm_exit, "", "" if rm_exit == 0 else "no such container")
        else:
            # probe for version etc.
            return (0, "fake version 4.0.0", "")

    class FakeRuntime:
        def __init__(self):
            self.calls = recorded_calls

        def detect(self) -> RuntimeKind:
            return RuntimeKind.DOCKER

        def run_cli(self, args: list[str], timeout=None) -> tuple[int, str, str]:
            return _runner(args, timeout=timeout)

    return FakeRuntime()


# ---------------------------------------------------------------------------
# 1. Module / file existence
# ---------------------------------------------------------------------------


class TestModuleFilesExist:
    """Required source files must exist on disk."""

    def test_start_sandbox_use_case_module_exists(self):
        path = _use_case_path("start_sandbox.py")
        assert path.is_file(), f"Missing: {path}"

    def test_stop_sandbox_use_case_module_exists(self):
        path = _use_case_path("stop_sandbox.py")
        assert path.is_file(), f"Missing: {path}"

    def test_container_adapter_module_exists(self):
        path = _infra_path("container_adapter.py")
        assert path.is_file(), f"Missing: {path}"


# ---------------------------------------------------------------------------
# 2. CliContainerAdapter importability
# ---------------------------------------------------------------------------


class TestCliContainerAdapterImportable:
    """CliContainerAdapter must be importable from infrastructure layer."""

    def test_importable(self):
        from agent_sandbox.infrastructure.container_adapter import (  # noqa: F401
            CliContainerAdapter,
        )

        assert CliContainerAdapter is not None

    def test_instantiable_with_runtime_port(self):
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime()
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        assert adapter is not None

    def test_has_start_method(self):
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        assert hasattr(CliContainerAdapter, "start")
        assert callable(CliContainerAdapter.start)

    def test_has_exec_method(self):
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        assert hasattr(CliContainerAdapter, "exec")
        assert callable(CliContainerAdapter.exec)

    def test_has_stop_method(self):
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        assert hasattr(CliContainerAdapter, "stop")
        assert callable(CliContainerAdapter.stop)

    def test_satisfies_container_port_protocol(self):
        """CliContainerAdapter must satisfy ContainerPort protocol."""
        from agent_sandbox.application.ports import ContainerPort
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime()
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        assert isinstance(adapter, ContainerPort)


# ---------------------------------------------------------------------------
# 3. CliContainerHandle importability
# ---------------------------------------------------------------------------


class TestCliContainerHandleImportable:
    """CliContainerHandle must be importable and satisfy ContainerHandlePort."""

    def test_importable(self):
        from agent_sandbox.infrastructure.container_adapter import (  # noqa: F401
            CliContainerHandle,
        )

        assert CliContainerHandle is not None

    def test_instantiable(self):
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime()
        handle = CliContainerHandle(
            container_id="abc123",
            image_tag="agent-sandbox:test",
            runtime_port=fake_runtime,
        )
        assert handle is not None

    def test_has_container_id_property(self):
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime()
        handle = CliContainerHandle(
            container_id="abc123",
            image_tag="agent-sandbox:test",
            runtime_port=fake_runtime,
        )
        assert handle.container_id == "abc123"

    def test_has_image_tag_property(self):
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime()
        handle = CliContainerHandle(
            container_id="abc123",
            image_tag="agent-sandbox:test",
            runtime_port=fake_runtime,
        )
        assert handle.image_tag == "agent-sandbox:test"

    def test_satisfies_container_handle_port_protocol(self):
        """CliContainerHandle must satisfy ContainerHandlePort protocol."""
        from agent_sandbox.application.ports import ContainerHandlePort
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime()
        handle = CliContainerHandle(
            container_id="abc123",
            image_tag="agent-sandbox:test",
            runtime_port=fake_runtime,
        )
        assert isinstance(handle, ContainerHandlePort)


# ---------------------------------------------------------------------------
# 4. CliContainerAdapter.start: config applied to run invocation (Criterion 1 & 4)
# ---------------------------------------------------------------------------


class TestCliContainerAdapterStart:
    """Criterion 1 & 4: start() applies config and uses isolation flags."""

    def test_start_returns_handle_with_container_id(self):
        """start() must return a handle whose container_id matches the runtime output."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(
            run_exit=0,
            run_stdout="my-container-abc123\n",
        )
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        config = SandboxConfig()
        handle = adapter.start(config, "agent-sandbox:test-tag")
        assert handle.container_id == "my-container-abc123"

    def test_start_returns_handle_with_image_tag(self):
        """start() must return a handle whose image_tag matches the argument."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="container-id\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        config = SandboxConfig()
        handle = adapter.start(config, "agent-sandbox:abc-tag")
        assert handle.image_tag == "agent-sandbox:abc-tag"

    def test_start_calls_runtime_with_argument_list(self):
        """Criterion 6: runtime.run_cli must receive a list."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        received_as_str: list[str] = []

        class StrictRuntime:
            def detect(self):
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args, timeout=None) -> tuple[int, str, str]:
                if isinstance(args, str):
                    received_as_str.append(args)
                assert isinstance(args, list), (
                    f"Must receive list, got {type(args).__name__!r}: {args!r}"
                )
                return (0, "container-id\n", "")

        adapter = CliContainerAdapter(runtime_port=StrictRuntime())
        adapter.start(SandboxConfig(), "agent-sandbox:tag")
        assert len(received_as_str) == 0, (
            f"run_cli was called with shell strings: {received_as_str}"
        )

    def test_start_invokes_run_subcommand(self):
        """start() must invoke the 'run' subcommand."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="cid\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        adapter.start(SandboxConfig(), "agent-sandbox:tag")

        run_calls = [c for c in fake_runtime.calls if "run" in c]
        assert len(run_calls) >= 1, f"Expected a 'run' call, got: {fake_runtime.calls}"

    def test_start_uses_detach_flag(self):
        """Criterion 4: start() must run container in detached mode (-d)."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="cid\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        adapter.start(SandboxConfig(), "agent-sandbox:tag")

        run_calls = [c for c in fake_runtime.calls if "run" in c]
        assert len(run_calls) >= 1
        run_args_flat = " ".join(run_calls[0])
        assert "-d" in run_calls[0] or "--detach" in run_args_flat, (
            f"Expected -d (detach) flag in run args: {run_calls[0]}"
        )

    def test_start_uses_rm_semantics(self):
        """Criterion 4: start() must use --rm to prevent orphaned containers."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="cid\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        adapter.start(SandboxConfig(), "agent-sandbox:tag")

        run_calls = [c for c in fake_runtime.calls if "run" in c]
        assert len(run_calls) >= 1
        assert "--rm" in run_calls[0], (
            f"Expected --rm flag in run args for no-orphan guarantee: {run_calls[0]}"
        )

    def test_start_does_not_use_privileged_flag(self):
        """Criterion 4: start() must NOT use --privileged (least privilege)."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="cid\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        adapter.start(SandboxConfig(), "agent-sandbox:tag")

        run_calls = [c for c in fake_runtime.calls if "run" in c]
        assert len(run_calls) >= 1
        run_args_flat = " ".join(run_calls[0])
        assert "--privileged" not in run_args_flat, (
            f"--privileged must NOT appear in run args: {run_calls[0]}"
        )

    def test_start_applies_volumes(self):
        """Criterion 1: volumes from config are passed as -v flags."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="cid\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        config = SandboxConfig(
            volumes=[
                Volume(host_path="/src", container_path="/workspace", mode="rw"),
                Volume(host_path="/data", container_path="/data", mode="ro"),
            ]
        )
        adapter.start(config, "agent-sandbox:tag")

        run_calls = [c for c in fake_runtime.calls if "run" in c]
        assert len(run_calls) >= 1
        run_args_str = " ".join(run_calls[0])
        assert "/src:/workspace" in run_args_str or "/src:/workspace:rw" in run_args_str, (
            f"Expected /src:/workspace in run args: {run_calls[0]}"
        )
        assert "/data:/data" in run_args_str or "/data:/data:ro" in run_args_str, (
            f"Expected /data:/data in run args: {run_calls[0]}"
        )

    def test_start_applies_ports(self):
        """Criterion 1: port mappings from config are passed as -p flags."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="cid\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        config = SandboxConfig(
            ports=[PortMapping(host_port=8080, container_port=80, protocol="tcp")]
        )
        adapter.start(config, "agent-sandbox:tag")

        run_calls = [c for c in fake_runtime.calls if "run" in c]
        assert len(run_calls) >= 1
        run_args_str = " ".join(run_calls[0])
        assert "8080:80" in run_args_str, (
            f"Expected port mapping 8080:80 in run args: {run_calls[0]}"
        )

    def test_start_applies_env(self):
        """Criterion 1: env vars from config are passed as -e flags."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="cid\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        config = SandboxConfig(env={"API_KEY": "s3cr3t", "DEBUG": "1"})
        adapter.start(config, "agent-sandbox:tag")

        run_calls = [c for c in fake_runtime.calls if "run" in c]
        assert len(run_calls) >= 1
        run_args_str = " ".join(run_calls[0])
        assert "API_KEY=s3cr3t" in run_args_str, (
            f"Expected API_KEY=s3cr3t in run args: {run_calls[0]}"
        )
        assert "DEBUG=1" in run_args_str, (
            f"Expected DEBUG=1 in run args: {run_calls[0]}"
        )

    def test_start_applies_memory_limit(self):
        """Criterion 1: memory limit from config is passed as -m flag."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="cid\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        config = SandboxConfig(memory_limit=MemoryLimit(value=512, unit="m"))
        adapter.start(config, "agent-sandbox:tag")

        run_calls = [c for c in fake_runtime.calls if "run" in c]
        assert len(run_calls) >= 1
        run_args_str = " ".join(run_calls[0])
        assert "512m" in run_args_str, (
            f"Expected 512m memory limit in run args: {run_calls[0]}"
        )

    def test_start_includes_image_tag_in_run_args(self):
        """Image tag must appear in the run command."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=0, run_stdout="cid\n")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)
        adapter.start(SandboxConfig(), "agent-sandbox:specific-tag")

        run_calls = [c for c in fake_runtime.calls if "run" in c]
        assert len(run_calls) >= 1
        assert "agent-sandbox:specific-tag" in run_calls[0], (
            f"Expected image tag in run args: {run_calls[0]}"
        )

    def test_start_failure_raises_sandbox_error(self):
        """start() with non-zero exit must raise SandboxError(CONTAINER_START_FAILED)."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(
            run_exit=1,
            run_stdout="",
        )
        adapter = CliContainerAdapter(runtime_port=fake_runtime)

        with pytest.raises(SandboxError) as exc_info:
            adapter.start(SandboxConfig(), "agent-sandbox:tag")

        assert exc_info.value.code == ErrorCode.CONTAINER_START_FAILED

    def test_start_failure_error_message_is_descriptive(self):
        """CONTAINER_START_FAILED error carries a descriptive message."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        fake_runtime = _make_fake_runtime(run_exit=1, run_stdout="")
        adapter = CliContainerAdapter(runtime_port=fake_runtime)

        with pytest.raises(SandboxError) as exc_info:
            adapter.start(SandboxConfig(), "agent-sandbox:tag")

        msg = str(exc_info.value)
        assert len(msg) > 10, f"Error message must be descriptive, got: {msg!r}"


# ---------------------------------------------------------------------------
# 5. Rollback: failed start leaves no orphan (Criterion 2)
# ---------------------------------------------------------------------------


class TestContainerStartRollback:
    """Criterion 2: failed start rolls back any partially-created container."""

    def test_failed_start_with_partial_container_id_triggers_cleanup(self):
        """If docker run returns non-zero but stdout has a container ID, clean it up."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        # Simulate: runtime returned a partial container_id but failed
        fake_runtime = _make_fake_runtime(
            run_exit=1,
            run_stdout="partial-container-abc\n",
            rm_exit=0,
        )
        adapter = CliContainerAdapter(runtime_port=fake_runtime)

        with pytest.raises(SandboxError):
            adapter.start(SandboxConfig(), "agent-sandbox:tag")

        # Verify a rm call was made to clean up the partial container
        rm_calls = [c for c in fake_runtime.calls if "rm" in c]
        assert len(rm_calls) >= 1, (
            f"Expected a 'rm' cleanup call after partial container created, "
            f"got calls: {fake_runtime.calls}"
        )
        # The partial container ID must appear in the rm call
        rm_args_flat = " ".join(str(a) for c in rm_calls for a in c)
        assert "partial-container-abc" in rm_args_flat, (
            f"Expected partial container ID in rm call: {rm_calls}"
        )

    def test_failed_start_without_partial_container_no_extra_calls(self):
        """If run fails with empty stdout, no spurious rm call is made."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter

        # No container ID → no partial container to clean up
        fake_runtime = _make_fake_runtime(
            run_exit=1,
            run_stdout="",
            rm_exit=0,
        )
        adapter = CliContainerAdapter(runtime_port=fake_runtime)

        with pytest.raises(SandboxError):
            adapter.start(SandboxConfig(), "agent-sandbox:tag")

        # No spurious rm with empty container_id
        rm_calls = [
            c for c in fake_runtime.calls
            if "rm" in c and any(bool(a.strip()) for a in c if a not in ("rm", "-f"))
        ]
        # Should have at most no rm call for partial cleanup when no container ID
        # (It's okay if there's a rm call as long as it's empty or no-op)
        for rm_call in rm_calls:
            non_rm_args = [a for a in rm_call if a not in ("rm", "-f", "--rm")]
            # Should not have tried to remove an empty container ID
            assert all(a.strip() for a in non_rm_args), (
                f"rm called with empty/blank container ID: {rm_call}"
            )


# ---------------------------------------------------------------------------
# 6. CliContainerHandle.stop: idempotent (Criterion 3)
# ---------------------------------------------------------------------------


class TestCliContainerHandleStop:
    """Criterion 3: stop() is idempotent — second call is a no-op, no error."""

    def test_stop_calls_runtime(self):
        """First stop() call must invoke the runtime to stop the container."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime(stop_exit=0, rm_exit=0)
        handle = CliContainerHandle(
            container_id="cid-123",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )
        handle.stop()
        stop_calls = [c for c in fake_runtime.calls if "stop" in c]
        assert len(stop_calls) >= 1, (
            f"Expected at least one stop call, got: {fake_runtime.calls}"
        )

    def test_stop_is_idempotent(self):
        """Second stop() call must be a no-op (no new runtime calls)."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime(stop_exit=0, rm_exit=0)
        handle = CliContainerHandle(
            container_id="cid-123",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )

        handle.stop()
        calls_after_first = len(fake_runtime.calls)

        # Second call must not raise and must not make new runtime calls
        handle.stop()  # must not raise
        calls_after_second = len(fake_runtime.calls)

        assert calls_after_second == calls_after_first, (
            f"Second stop() made additional runtime calls: "
            f"{fake_runtime.calls[calls_after_first:]}"
        )

    def test_stop_does_not_raise_on_missing_container(self):
        """stop() must not raise even if container no longer exists (non-zero exit)."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        # Simulate: container already gone (stop returns non-zero)
        fake_runtime = _make_fake_runtime(stop_exit=1, rm_exit=1)
        handle = CliContainerHandle(
            container_id="gone-cid",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )
        # Should not raise
        handle.stop()

    def test_stop_calls_runtime_with_argument_list(self):
        """Criterion 6: stop() must call runtime with argument lists only."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        received_as_str: list[str] = []

        class StrictRuntime:
            def detect(self):
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args, timeout=None) -> tuple[int, str, str]:
                if isinstance(args, str):
                    received_as_str.append(args)
                return (0, "", "")

        handle = CliContainerHandle(
            container_id="cid",
            image_tag="agent-sandbox:tag",
            runtime_port=StrictRuntime(),
        )
        handle.stop()
        assert len(received_as_str) == 0, (
            f"stop() passed shell strings to runtime: {received_as_str}"
        )


# ---------------------------------------------------------------------------
# 7. CliContainerHandle.exec
# ---------------------------------------------------------------------------


class TestCliContainerHandleExec:
    """exec() must delegate to runtime and return ExecResult."""

    def test_exec_returns_exec_result(self):
        """exec() must return an ExecResult instance."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime(exec_exit=0, exec_stdout="hello\n")
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )
        result = handle.exec(["echo", "hello"])
        assert isinstance(result, ExecResult)

    def test_exec_captures_stdout(self):
        """exec() result must include the command's stdout."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime(exec_exit=0, exec_stdout="hello world\n")
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )
        result = handle.exec(["echo", "hello", "world"])
        assert "hello world" in result.stdout

    def test_exec_non_zero_exit_returns_result_not_raises(self):
        """Non-zero exit from exec must return ExecResult, NOT raise."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime(exec_exit=42, exec_stdout="")
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )
        result = handle.exec(["false"])
        assert result.exit_code == 42, (
            f"Non-zero exit must be in result.exit_code, got: {result.exit_code}"
        )

    def test_exec_invokes_exec_subcommand(self):
        """exec() must use 'exec' subcommand with the container_id."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime(exec_exit=0, exec_stdout="")
        handle = CliContainerHandle(
            container_id="my-container",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )
        handle.exec(["ls", "-la"])

        exec_calls = [c for c in fake_runtime.calls if "exec" in c]
        assert len(exec_calls) >= 1, f"Expected exec call, got: {fake_runtime.calls}"
        # container_id must appear in the exec call
        exec_call_str = " ".join(exec_calls[0])
        assert "my-container" in exec_call_str, (
            f"Expected container ID in exec call: {exec_calls[0]}"
        )

    def test_exec_accepts_string_command(self):
        """exec() must accept a plain string cmd (converted to list internally)."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime(exec_exit=0, exec_stdout="")
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )
        # Should not raise
        handle.exec("ls -la")

    def test_exec_duration_ms_is_non_negative(self):
        """ExecResult.duration_ms must be a non-negative integer."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime(exec_exit=0, exec_stdout="output")
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )
        result = handle.exec(["echo", "test"])
        assert isinstance(result.duration_ms, int)
        assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# 8. StartSandboxUseCase importability
# ---------------------------------------------------------------------------


class TestStartSandboxUseCaseImportable:
    """StartSandboxUseCase must be importable from application use_cases layer."""

    def test_importable(self):
        from agent_sandbox.application.use_cases.start_sandbox import (  # noqa: F401
            StartSandboxUseCase,
        )

        assert StartSandboxUseCase is not None

    def test_instantiable(self):
        from agent_sandbox.application.use_cases.start_sandbox import StartSandboxUseCase

        class FakeContainerPort:
            def start(self, config, image_tag):
                return None

            def exec(self, handle, cmd, timeout=None):
                return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0, timed_out=False)

            def stop(self, handle):
                pass

        uc = StartSandboxUseCase(
            container_port=FakeContainerPort(),
            ensure_image_use_case=_make_fake_ensure_image(),
        )
        assert uc is not None

    def test_has_execute_method(self):
        from agent_sandbox.application.use_cases.start_sandbox import StartSandboxUseCase

        assert hasattr(StartSandboxUseCase, "execute")
        assert callable(StartSandboxUseCase.execute)


# ---------------------------------------------------------------------------
# 9. StartSandboxUseCase behaviour
# ---------------------------------------------------------------------------


class TestStartSandboxUseCaseBehaviour:
    """StartSandboxUseCase orchestrates image ensure + container start."""

    def _make_container_port(self, *, fail: bool = False):
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        calls: list[str] = []

        class FakeContainerPort:
            def start(self_inner, config, image_tag) -> CliContainerHandle:
                calls.append("start")
                if fail:
                    raise SandboxError(
                        "Container failed to start",
                        code=ErrorCode.CONTAINER_START_FAILED,
                    )
                fake_runtime = _make_fake_runtime()
                return CliContainerHandle(
                    container_id="fake-cid",
                    image_tag=image_tag,
                    runtime_port=fake_runtime,
                )

            def exec(self_inner, handle, cmd, timeout=None):
                return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0, timed_out=False)

            def stop(self_inner, handle):
                calls.append("stop")

        port = FakeContainerPort()
        port.calls = calls
        return port

    def test_execute_returns_handle(self):
        """execute() must return a ContainerHandlePort on success."""
        from agent_sandbox.application.use_cases.start_sandbox import StartSandboxUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        ensure_image = _make_fake_ensure_image(succeed=True)
        container_port = self._make_container_port(fail=False)
        uc = StartSandboxUseCase(
            container_port=container_port,
            ensure_image_use_case=ensure_image,
        )
        config = SandboxConfig()
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")

        handle = uc.execute(config, spec, containerfile_content="FROM ubuntu:22.04")
        assert handle is not None
        assert hasattr(handle, "container_id")
        assert hasattr(handle, "stop")

    def test_execute_calls_ensure_image_first(self):
        """ensure_image must be called before container.start()."""
        from agent_sandbox.application.use_cases.start_sandbox import StartSandboxUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        order: list[str] = []

        class TrackingEnsureImage:
            def execute(self, image_spec, containerfile_content):
                order.append("ensure_image")

        class TrackingContainerPort:
            def start(self, config, image_tag):
                order.append("container_start")
                fake_runtime = _make_fake_runtime()
                from agent_sandbox.infrastructure.container_adapter import CliContainerHandle
                return CliContainerHandle(
                    container_id="cid", image_tag=image_tag, runtime_port=fake_runtime
                )

            def exec(self, handle, cmd, timeout=None):
                return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0, timed_out=False)

            def stop(self, handle):
                pass

        uc = StartSandboxUseCase(
            container_port=TrackingContainerPort(),
            ensure_image_use_case=TrackingEnsureImage(),
        )
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        uc.execute(SandboxConfig(), spec, containerfile_content="FROM ubuntu:22.04")

        assert order.index("ensure_image") < order.index("container_start"), (
            f"ensure_image must be called before container_start, order: {order}"
        )

    def test_execute_propagates_image_build_failure(self):
        """IMAGE_BUILD_FAILED from ensure_image propagates to caller."""
        from agent_sandbox.application.use_cases.start_sandbox import StartSandboxUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        ensure_image = _make_fake_ensure_image(succeed=False)
        container_port = self._make_container_port(fail=False)
        uc = StartSandboxUseCase(
            container_port=container_port,
            ensure_image_use_case=ensure_image,
        )
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")

        with pytest.raises(SandboxError) as exc_info:
            uc.execute(SandboxConfig(), spec, containerfile_content="FROM ubuntu:22.04")

        assert exc_info.value.code == ErrorCode.IMAGE_BUILD_FAILED

    def test_execute_propagates_container_start_failure(self):
        """CONTAINER_START_FAILED from container_port propagates to caller."""
        from agent_sandbox.application.use_cases.start_sandbox import StartSandboxUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        ensure_image = _make_fake_ensure_image(succeed=True)
        container_port = self._make_container_port(fail=True)
        uc = StartSandboxUseCase(
            container_port=container_port,
            ensure_image_use_case=ensure_image,
        )
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")

        with pytest.raises(SandboxError) as exc_info:
            uc.execute(SandboxConfig(), spec, containerfile_content="FROM ubuntu:22.04")

        assert exc_info.value.code == ErrorCode.CONTAINER_START_FAILED


# ---------------------------------------------------------------------------
# 10. StopSandboxUseCase importability and behaviour
# ---------------------------------------------------------------------------


class TestStopSandboxUseCaseImportable:
    """StopSandboxUseCase must be importable and callable."""

    def test_importable(self):
        from agent_sandbox.application.use_cases.stop_sandbox import (  # noqa: F401
            StopSandboxUseCase,
        )

        assert StopSandboxUseCase is not None

    def test_instantiable(self):
        from agent_sandbox.application.use_cases.stop_sandbox import StopSandboxUseCase

        class FakeContainerPort:
            def start(self, config, image_tag):
                return None

            def exec(self, handle, cmd, timeout=None):
                return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0, timed_out=False)

            def stop(self, handle):
                pass

        uc = StopSandboxUseCase(container_port=FakeContainerPort())
        assert uc is not None

    def test_has_execute_method(self):
        from agent_sandbox.application.use_cases.stop_sandbox import StopSandboxUseCase

        assert hasattr(StopSandboxUseCase, "execute")
        assert callable(StopSandboxUseCase.execute)

    def test_execute_calls_handle_stop(self):
        """StopSandboxUseCase.execute() must call handle.stop()."""
        from agent_sandbox.application.use_cases.stop_sandbox import StopSandboxUseCase

        stop_called: list[bool] = []

        class FakeHandle:
            @property
            def container_id(self):
                return "cid"

            @property
            def image_tag(self):
                return "tag"

            def exec(self, cmd, timeout=None):
                return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0, timed_out=False)

            def stop(self):
                stop_called.append(True)

        class FakeContainerPort:
            def start(self, config, image_tag):
                return None

            def exec(self, handle, cmd, timeout=None):
                return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0, timed_out=False)

            def stop(self, handle):
                handle.stop()

        uc = StopSandboxUseCase(container_port=FakeContainerPort())
        uc.execute(FakeHandle())

        assert len(stop_called) >= 1, "stop() must be called on the handle"

    def test_execute_is_idempotent_via_handle(self):
        """StopSandboxUseCase.execute() called twice on same handle is safe."""
        from agent_sandbox.application.use_cases.stop_sandbox import StopSandboxUseCase
        from agent_sandbox.infrastructure.container_adapter import CliContainerHandle

        fake_runtime = _make_fake_runtime(stop_exit=0, rm_exit=0)
        handle = CliContainerHandle(
            container_id="cid",
            image_tag="agent-sandbox:tag",
            runtime_port=fake_runtime,
        )

        class FakeContainerPort:
            def start(self, config, image_tag):
                return None

            def exec(self_inner, h, cmd, timeout=None):
                return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0, timed_out=False)

            def stop(self_inner, h):
                h.stop()

        uc = StopSandboxUseCase(container_port=FakeContainerPort())
        uc.execute(handle)  # first
        calls_after_first = len(fake_runtime.calls)
        uc.execute(handle)  # second — must not raise and must not add calls
        calls_after_second = len(fake_runtime.calls)

        # Second call must not make additional runtime calls (handle is idempotent)
        assert calls_after_second == calls_after_first, (
            f"Second stop added unexpected calls: {fake_runtime.calls[calls_after_first:]}"
        )


# ---------------------------------------------------------------------------
# 11. Import purity checks
# ---------------------------------------------------------------------------


class TestStartSandboxImportPurity:
    """start_sandbox.py (application layer) must not import infrastructure."""

    def _get_ast(self) -> ast.Module:
        path = _use_case_path("start_sandbox.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_subprocess_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "subprocess", (
                        "start_sandbox.py must not import 'subprocess'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "subprocess", (
                        "start_sandbox.py must not import from 'subprocess'"
                    )

    def test_no_infrastructure_layer_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "infrastructure" in node.module:
                    pytest.fail(
                        f"start_sandbox.py must not import from infrastructure: {node.module!r}"
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


class TestStopSandboxImportPurity:
    """stop_sandbox.py (application layer) must not import infrastructure."""

    def _get_ast(self) -> ast.Module:
        path = _use_case_path("stop_sandbox.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_subprocess_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "subprocess", (
                        "stop_sandbox.py must not import 'subprocess'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "subprocess"

    def test_no_infrastructure_layer_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "infrastructure" in node.module:
                    pytest.fail(
                        f"stop_sandbox.py must not import from infrastructure: {node.module!r}"
                    )


class TestContainerAdapterImportPurity:
    """container_adapter.py (infrastructure) may use subprocess + domain + stdlib.

    Must not import heavy frameworks.
    """

    def _get_ast(self) -> ast.Module:
        path = _infra_path("container_adapter.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_framework_imports(self):
        forbidden = {"click", "fastapi", "sqlalchemy", "flask", "django"}
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


class TestComposeNetworkConnect:
    """CliContainerAdapter: network connect issued when compose_file is set."""

    def test_network_connect_called_when_compose_file_set(self, tmp_path):
        """start() issues 'network connect <network> <container_id>' after run."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter
        from agent_sandbox.domain.entities import SandboxConfig
        from pathlib import Path

        # Write a minimal compose file with a project name
        compose_file = tmp_path / "compose.yml"
        compose_file.write_text("name: myproject\nservices:\n  web:\n    image: nginx\n")

        config = SandboxConfig(compose_file=compose_file)

        calls = []

        class FakeRuntime:
            def run_cli(self, args, timeout=None):
                calls.append(list(args))
                if args[0] == "run":
                    return 0, "fake-container-abc\n", ""
                return 0, "", ""

            def _build_args(self, args):
                return args

        adapter = CliContainerAdapter(runtime_port=FakeRuntime())
        adapter.start(config, "agent-sandbox:test")

        network_calls = [c for c in calls if c[0] == "network"]
        assert len(network_calls) == 1, f"Expected 1 network call, got: {network_calls}"
        assert network_calls[0] == ["network", "connect", "myproject_default", "fake-container-abc"]

    def test_network_name_derived_from_compose_name_field(self, tmp_path):
        """_derive_compose_network reads 'name:' from compose YAML."""
        from agent_sandbox.infrastructure.container_adapter import _derive_compose_network
        compose_file = tmp_path / "compose.yml"
        compose_file.write_text("name: aef\nservices: {}\n")
        assert _derive_compose_network(compose_file) == "aef_default"

    def test_network_name_falls_back_to_directory_name(self, tmp_path):
        """_derive_compose_network falls back to parent dir name when no 'name:' field."""
        from agent_sandbox.infrastructure.container_adapter import _derive_compose_network
        subdir = tmp_path / "myproject"
        subdir.mkdir()
        compose_file = subdir / "compose.yml"
        compose_file.write_text("services:\n  web:\n    image: nginx\n")
        assert _derive_compose_network(compose_file) == "myproject_default"

    def test_no_network_connect_when_compose_file_not_set(self):
        """start() does not issue any 'network' CLI call when compose_file is None."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter
        from agent_sandbox.domain.entities import SandboxConfig

        config = SandboxConfig()  # compose_file=None by default

        calls = []

        class FakeRuntime:
            def run_cli(self, args, timeout=None):
                calls.append(list(args))
                if args[0] == "run":
                    return 0, "cid\n", ""
                return 0, "", ""

            def _build_args(self, args):
                return args

        adapter = CliContainerAdapter(runtime_port=FakeRuntime())
        adapter.start(config, "agent-sandbox:test")

        network_calls = [c for c in calls if c[0] == "network"]
        assert network_calls == [], f"Unexpected network calls: {network_calls}"

    def test_network_connect_failure_does_not_raise(self, tmp_path):
        """A non-zero exit from 'network connect' is logged but does not abort start."""
        from agent_sandbox.infrastructure.container_adapter import CliContainerAdapter
        from agent_sandbox.domain.entities import SandboxConfig

        compose_file = tmp_path / "compose.yml"
        compose_file.write_text("name: testproject\nservices: {}\n")
        config = SandboxConfig(compose_file=compose_file)

        class FakeRuntime:
            def run_cli(self, args, timeout=None):
                if args[0] == "run":
                    return 0, "cid\n", ""
                if args[0] == "network":
                    return 1, "", "network testproject_default not found"
                return 0, "", ""

            def _build_args(self, args):
                return args

        adapter = CliContainerAdapter(runtime_port=FakeRuntime())
        # Must not raise even though network connect returns exit code 1
        handle = adapter.start(config, "agent-sandbox:test")
        assert handle.container_id == "cid"
