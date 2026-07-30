"""Tests for domain entities (FEAT-002).

TDD: tests are written before the implementation.
Domain layer must be framework-free (no subprocess, click, docker, etc.).
"""
import ast
import os
import pytest
from pathlib import Path


class TestSandboxConfig:
    """SandboxConfig aggregate entity."""

    def test_sandbox_config_can_be_created_with_no_args(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig()
        assert cfg is not None

    def test_sandbox_config_default_volumes_is_empty(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.volumes == []

    def test_sandbox_config_default_ports_is_empty(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.ports == []

    def test_sandbox_config_default_env_is_empty(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.env == {}

    def test_sandbox_config_default_mise_is_false(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.mise is False

    def test_sandbox_config_default_memory_limit_is_none(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.memory_limit is None

    def test_sandbox_config_default_runtime_is_auto(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.domain.value_objects import RuntimeKind
        cfg = SandboxConfig()
        assert cfg.runtime == RuntimeKind.AUTO

    def test_sandbox_config_default_config_path_is_none(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.config_path is None

    def test_sandbox_config_default_source_filename_is_empty(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.source_filename == ""

    def test_sandbox_config_default_claude_config_dir_is_none(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.claude_config_dir is None

    def test_sandbox_config_accepts_claude_config_dir(self):
        from pathlib import Path
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig(claude_config_dir=Path("/home/alice/.claude-acme"))
        assert cfg.claude_config_dir == Path("/home/alice/.claude-acme")

    def test_sandbox_config_accepts_volumes(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.domain.value_objects import Volume
        vol = Volume(host_path="/src", container_path="/workspace")
        cfg = SandboxConfig(volumes=[vol])
        assert len(cfg.volumes) == 1
        assert cfg.volumes[0] == vol

    def test_sandbox_config_accepts_ports(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.domain.value_objects import PortMapping
        port = PortMapping(host_port=8080, container_port=80)
        cfg = SandboxConfig(ports=[port])
        assert len(cfg.ports) == 1
        assert cfg.ports[0] == port

    def test_sandbox_config_accepts_env(self):
        from agent_sandbox.domain.entities import SandboxConfig
        env = {"API_KEY": "secret", "DEBUG": "1"}
        cfg = SandboxConfig(env=env)
        assert cfg.env["API_KEY"] == "secret"
        assert cfg.env["DEBUG"] == "1"

    def test_sandbox_config_accepts_mise_true(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig(mise=True)
        assert cfg.mise is True

    def test_sandbox_config_accepts_memory_limit(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.domain.value_objects import MemoryLimit
        mem = MemoryLimit(value=512, unit="m")
        cfg = SandboxConfig(memory_limit=mem)
        assert cfg.memory_limit is not None
        assert cfg.memory_limit.value == 512

    def test_sandbox_config_accepts_runtime_docker(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.domain.value_objects import RuntimeKind
        cfg = SandboxConfig(runtime=RuntimeKind.DOCKER)
        assert cfg.runtime == RuntimeKind.DOCKER

    def test_sandbox_config_accepts_runtime_podman(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.domain.value_objects import RuntimeKind
        cfg = SandboxConfig(runtime=RuntimeKind.PODMAN)
        assert cfg.runtime == RuntimeKind.PODMAN

    def test_sandbox_config_accepts_config_path(self):
        from agent_sandbox.domain.entities import SandboxConfig
        path = Path("/project/.agent-sandbox")
        cfg = SandboxConfig(config_path=path)
        assert cfg.config_path == path

    def test_sandbox_config_accepts_source_filename(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig(source_filename=".agent-sandbox")
        assert cfg.source_filename == ".agent-sandbox"

    def test_sandbox_config_multiple_volumes(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.domain.value_objects import Volume
        vols = [
            Volume(host_path="/src", container_path="/workspace"),
            Volume(host_path="/data", container_path="/data", mode="ro"),
        ]
        cfg = SandboxConfig(volumes=vols)
        assert len(cfg.volumes) == 2

    def test_sandbox_config_multiple_ports(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.domain.value_objects import PortMapping
        ports = [
            PortMapping(host_port=8080, container_port=80),
            PortMapping(host_port=5432, container_port=5432),
        ]
        cfg = SandboxConfig(ports=ports)
        assert len(cfg.ports) == 2


class TestExecResult:
    """ExecResult entity: exit_code, stdout, stderr, duration_ms, timed_out."""

    def test_exec_result_can_be_created(self):
        from agent_sandbox.domain.entities import ExecResult
        result = ExecResult(
            exit_code=0,
            stdout="hello",
            stderr="",
            duration_ms=42,
            timed_out=False,
        )
        assert result is not None

    def test_exec_result_exit_code(self):
        from agent_sandbox.domain.entities import ExecResult
        result = ExecResult(exit_code=1, stdout="", stderr="err", duration_ms=10, timed_out=False)
        assert result.exit_code == 1

    def test_exec_result_stdout(self):
        from agent_sandbox.domain.entities import ExecResult
        result = ExecResult(exit_code=0, stdout="output text", stderr="", duration_ms=5, timed_out=False)
        assert result.stdout == "output text"

    def test_exec_result_stderr(self):
        from agent_sandbox.domain.entities import ExecResult
        result = ExecResult(exit_code=0, stdout="", stderr="warning", duration_ms=5, timed_out=False)
        assert result.stderr == "warning"

    def test_exec_result_duration_ms(self):
        from agent_sandbox.domain.entities import ExecResult
        result = ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1234, timed_out=False)
        assert result.duration_ms == 1234

    def test_exec_result_timed_out_false(self):
        from agent_sandbox.domain.entities import ExecResult
        result = ExecResult(exit_code=0, stdout="", stderr="", duration_ms=10, timed_out=False)
        assert result.timed_out is False

    def test_exec_result_timed_out_true(self):
        from agent_sandbox.domain.entities import ExecResult
        result = ExecResult(exit_code=-1, stdout="", stderr="killed", duration_ms=30000, timed_out=True)
        assert result.timed_out is True

    def test_exec_result_non_zero_exit_not_error(self):
        """Non-zero exit code is NOT an error — it's returned in ExecResult."""
        from agent_sandbox.domain.entities import ExecResult
        # Should not raise; non-zero exit is valid
        result = ExecResult(exit_code=127, stdout="", stderr="command not found", duration_ms=1, timed_out=False)
        assert result.exit_code == 127

    def test_exec_result_is_frozen(self):
        from agent_sandbox.domain.entities import ExecResult
        result = ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1, timed_out=False)
        with pytest.raises((AttributeError, TypeError)):
            result.exit_code = 1  # type: ignore[misc]


class TestContainerHandle:
    """ContainerHandle entity: container_id, image_tag, runtime, state."""

    def test_container_handle_can_be_created(self):
        from agent_sandbox.domain.entities import ContainerHandle, ContainerState
        from agent_sandbox.domain.value_objects import RuntimeKind
        handle = ContainerHandle(
            container_id="abc123",
            image_tag="sandbox:latest",
            runtime=RuntimeKind.DOCKER,
            state=ContainerState.RUNNING,
        )
        assert handle is not None

    def test_container_handle_container_id(self):
        from agent_sandbox.domain.entities import ContainerHandle, ContainerState
        from agent_sandbox.domain.value_objects import RuntimeKind
        handle = ContainerHandle(
            container_id="def456",
            image_tag="sandbox:v1",
            runtime=RuntimeKind.PODMAN,
            state=ContainerState.CREATED,
        )
        assert handle.container_id == "def456"

    def test_container_handle_image_tag(self):
        from agent_sandbox.domain.entities import ContainerHandle, ContainerState
        from agent_sandbox.domain.value_objects import RuntimeKind
        handle = ContainerHandle(
            container_id="abc",
            image_tag="my-image:sha256-abc123",
            runtime=RuntimeKind.DOCKER,
            state=ContainerState.RUNNING,
        )
        assert handle.image_tag == "my-image:sha256-abc123"

    def test_container_handle_runtime(self):
        from agent_sandbox.domain.entities import ContainerHandle, ContainerState
        from agent_sandbox.domain.value_objects import RuntimeKind
        handle = ContainerHandle(
            container_id="abc",
            image_tag="image:tag",
            runtime=RuntimeKind.PODMAN,
            state=ContainerState.RUNNING,
        )
        assert handle.runtime == RuntimeKind.PODMAN

    def test_container_handle_state_created(self):
        from agent_sandbox.domain.entities import ContainerHandle, ContainerState
        from agent_sandbox.domain.value_objects import RuntimeKind
        handle = ContainerHandle(
            container_id="abc",
            image_tag="image:tag",
            runtime=RuntimeKind.DOCKER,
            state=ContainerState.CREATED,
        )
        assert handle.state == ContainerState.CREATED

    def test_container_handle_state_running(self):
        from agent_sandbox.domain.entities import ContainerHandle, ContainerState
        from agent_sandbox.domain.value_objects import RuntimeKind
        handle = ContainerHandle(
            container_id="abc",
            image_tag="image:tag",
            runtime=RuntimeKind.DOCKER,
            state=ContainerState.RUNNING,
        )
        assert handle.state == ContainerState.RUNNING

    def test_container_handle_state_stopped(self):
        from agent_sandbox.domain.entities import ContainerHandle, ContainerState
        from agent_sandbox.domain.value_objects import RuntimeKind
        handle = ContainerHandle(
            container_id="abc",
            image_tag="image:tag",
            runtime=RuntimeKind.DOCKER,
            state=ContainerState.STOPPED,
        )
        assert handle.state == ContainerState.STOPPED

    def test_container_state_is_enum(self):
        import enum
        from agent_sandbox.domain.entities import ContainerState
        assert issubclass(ContainerState, enum.Enum)

    def test_container_state_has_three_members(self):
        from agent_sandbox.domain.entities import ContainerState
        members = {s.value for s in ContainerState}
        assert "created" in members
        assert "running" in members
        assert "stopped" in members

    def test_container_handle_is_frozen(self):
        from agent_sandbox.domain.entities import ContainerHandle, ContainerState
        from agent_sandbox.domain.value_objects import RuntimeKind
        handle = ContainerHandle(
            container_id="abc",
            image_tag="image:tag",
            runtime=RuntimeKind.DOCKER,
            state=ContainerState.RUNNING,
        )
        with pytest.raises((AttributeError, TypeError)):
            handle.container_id = "xyz"  # type: ignore[misc]


class TestNoFrameworkImports:
    """entities.py must import no framework modules (domain layer)."""

    def _get_module_path(self) -> str:
        src_dir = os.path.join(os.path.dirname(__file__), "..", "..", "src")
        return os.path.join(src_dir, "agent_sandbox", "domain", "entities.py")

    def test_entities_file_exists(self):
        path = self._get_module_path()
        assert os.path.isfile(path), f"entities.py not found at {path}"

    def test_no_forbidden_imports(self):
        path = self._get_module_path()
        with open(path) as f:
            tree = ast.parse(f.read())
        forbidden = {
            "subprocess", "click", "fastapi", "sqlalchemy", "flask",
            "django", "docker", "podman",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, (
                        f"entities.py must not import '{top}' (domain layer)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"entities.py must not import from '{top}' (domain layer)"
                    )
