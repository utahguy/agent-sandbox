"""Tests for domain value objects (FEAT-002).

TDD: tests are written before the implementation.
Domain layer must be framework-free (no subprocess, click, docker, etc.).
"""
import ast
import os
import pytest


class TestRuntimeKind:
    """RuntimeKind enum must expose AUTO/DOCKER/PODMAN members."""

    def test_runtime_kind_has_auto(self):
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert RuntimeKind.AUTO is not None

    def test_runtime_kind_has_docker(self):
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert RuntimeKind.DOCKER is not None

    def test_runtime_kind_has_podman(self):
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert RuntimeKind.PODMAN is not None

    def test_runtime_kind_is_enum(self):
        import enum
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert issubclass(RuntimeKind, enum.Enum)

    def test_runtime_kind_values_are_strings(self):
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert isinstance(RuntimeKind.AUTO.value, str)
        assert isinstance(RuntimeKind.DOCKER.value, str)
        assert isinstance(RuntimeKind.PODMAN.value, str)

    def test_runtime_kind_auto_value(self):
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert RuntimeKind.AUTO.value == "AUTO"

    def test_runtime_kind_docker_value(self):
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert RuntimeKind.DOCKER.value == "DOCKER"

    def test_runtime_kind_podman_value(self):
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert RuntimeKind.PODMAN.value == "PODMAN"


class TestVolume:
    """Volume value object: host_path, container_path, mode (ro/rw), selinux_relabel."""

    def test_volume_can_be_created_with_defaults(self):
        from agent_sandbox.domain.value_objects import Volume
        v = Volume(host_path="/host/path", container_path="/container/path")
        assert v.host_path == "/host/path"
        assert v.container_path == "/container/path"

    def test_volume_default_mode_is_rw(self):
        from agent_sandbox.domain.value_objects import Volume
        v = Volume(host_path="/src", container_path="/dst")
        assert v.mode == "rw"

    def test_volume_default_selinux_relabel_is_false(self):
        from agent_sandbox.domain.value_objects import Volume
        v = Volume(host_path="/src", container_path="/dst")
        assert v.selinux_relabel is False

    def test_volume_accepts_mode_ro(self):
        from agent_sandbox.domain.value_objects import Volume
        v = Volume(host_path="/src", container_path="/dst", mode="ro")
        assert v.mode == "ro"

    def test_volume_accepts_mode_rw(self):
        from agent_sandbox.domain.value_objects import Volume
        v = Volume(host_path="/src", container_path="/dst", mode="rw")
        assert v.mode == "rw"

    def test_volume_rejects_invalid_mode(self):
        from agent_sandbox.domain.value_objects import Volume
        from agent_sandbox.exceptions import SandboxError, ErrorCode
        with pytest.raises(SandboxError) as exc_info:
            Volume(host_path="/src", container_path="/dst", mode="invalid")
        assert exc_info.value.code == ErrorCode.CONFIG_MALFORMED

    def test_volume_rejects_mode_read(self):
        from agent_sandbox.domain.value_objects import Volume
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError):
            Volume(host_path="/src", container_path="/dst", mode="read")

    def test_volume_accepts_selinux_relabel_true(self):
        from agent_sandbox.domain.value_objects import Volume
        v = Volume(host_path="/src", container_path="/dst", selinux_relabel=True)
        assert v.selinux_relabel is True

    def test_volume_is_frozen(self):
        """Volume must be immutable (frozen dataclass or equivalent)."""
        from agent_sandbox.domain.value_objects import Volume
        v = Volume(host_path="/src", container_path="/dst")
        with pytest.raises((AttributeError, TypeError)):
            v.host_path = "/other"  # type: ignore[misc]


class TestPortMapping:
    """PortMapping value object with port range validation."""

    def test_port_mapping_can_be_created(self):
        from agent_sandbox.domain.value_objects import PortMapping
        p = PortMapping(host_port=8080, container_port=80)
        assert p.host_port == 8080
        assert p.container_port == 80

    def test_port_mapping_default_protocol_is_tcp(self):
        from agent_sandbox.domain.value_objects import PortMapping
        p = PortMapping(host_port=8080, container_port=80)
        assert p.protocol == "tcp"

    def test_port_mapping_accepts_tcp(self):
        from agent_sandbox.domain.value_objects import PortMapping
        p = PortMapping(host_port=8080, container_port=80, protocol="tcp")
        assert p.protocol == "tcp"

    def test_port_mapping_accepts_udp(self):
        from agent_sandbox.domain.value_objects import PortMapping
        p = PortMapping(host_port=5353, container_port=5353, protocol="udp")
        assert p.protocol == "udp"

    def test_port_mapping_rejects_invalid_protocol(self):
        from agent_sandbox.domain.value_objects import PortMapping
        from agent_sandbox.exceptions import SandboxError, ErrorCode
        with pytest.raises(SandboxError) as exc_info:
            PortMapping(host_port=8080, container_port=80, protocol="http")
        assert exc_info.value.code == ErrorCode.CONFIG_MALFORMED

    def test_port_mapping_rejects_host_port_zero(self):
        from agent_sandbox.domain.value_objects import PortMapping
        from agent_sandbox.exceptions import SandboxError, ErrorCode
        with pytest.raises(SandboxError) as exc_info:
            PortMapping(host_port=0, container_port=80)
        assert exc_info.value.code == ErrorCode.CONFIG_MALFORMED

    def test_port_mapping_rejects_host_port_negative(self):
        from agent_sandbox.domain.value_objects import PortMapping
        from agent_sandbox.exceptions import SandboxError, ErrorCode
        with pytest.raises(SandboxError) as exc_info:
            PortMapping(host_port=-1, container_port=80)
        assert exc_info.value.code == ErrorCode.CONFIG_MALFORMED

    def test_port_mapping_rejects_host_port_above_65535(self):
        from agent_sandbox.domain.value_objects import PortMapping
        from agent_sandbox.exceptions import SandboxError, ErrorCode
        with pytest.raises(SandboxError) as exc_info:
            PortMapping(host_port=65536, container_port=80)
        assert exc_info.value.code == ErrorCode.CONFIG_MALFORMED

    def test_port_mapping_rejects_container_port_zero(self):
        from agent_sandbox.domain.value_objects import PortMapping
        from agent_sandbox.exceptions import SandboxError, ErrorCode
        with pytest.raises(SandboxError) as exc_info:
            PortMapping(host_port=8080, container_port=0)
        assert exc_info.value.code == ErrorCode.CONFIG_MALFORMED

    def test_port_mapping_rejects_container_port_above_65535(self):
        from agent_sandbox.domain.value_objects import PortMapping
        from agent_sandbox.exceptions import SandboxError, ErrorCode
        with pytest.raises(SandboxError) as exc_info:
            PortMapping(host_port=8080, container_port=65536)
        assert exc_info.value.code == ErrorCode.CONFIG_MALFORMED

    def test_port_mapping_accepts_port_1(self):
        from agent_sandbox.domain.value_objects import PortMapping
        p = PortMapping(host_port=1, container_port=1)
        assert p.host_port == 1

    def test_port_mapping_accepts_port_65535(self):
        from agent_sandbox.domain.value_objects import PortMapping
        p = PortMapping(host_port=65535, container_port=65535)
        assert p.host_port == 65535

    def test_port_mapping_is_frozen(self):
        from agent_sandbox.domain.value_objects import PortMapping
        p = PortMapping(host_port=8080, container_port=80)
        with pytest.raises((AttributeError, TypeError)):
            p.host_port = 9090  # type: ignore[misc]


class TestMemoryLimit:
    """MemoryLimit value object: value + unit (b/k/m/g)."""

    def test_memory_limit_can_be_created(self):
        from agent_sandbox.domain.value_objects import MemoryLimit
        m = MemoryLimit(value=512, unit="m")
        assert m.value == 512
        assert m.unit == "m"

    def test_memory_limit_accepts_bytes(self):
        from agent_sandbox.domain.value_objects import MemoryLimit
        m = MemoryLimit(value=1024, unit="b")
        assert m.unit == "b"

    def test_memory_limit_accepts_kilobytes(self):
        from agent_sandbox.domain.value_objects import MemoryLimit
        m = MemoryLimit(value=1024, unit="k")
        assert m.unit == "k"

    def test_memory_limit_accepts_megabytes(self):
        from agent_sandbox.domain.value_objects import MemoryLimit
        m = MemoryLimit(value=512, unit="m")
        assert m.unit == "m"

    def test_memory_limit_accepts_gigabytes(self):
        from agent_sandbox.domain.value_objects import MemoryLimit
        m = MemoryLimit(value=2, unit="g")
        assert m.unit == "g"

    def test_memory_limit_rejects_invalid_unit(self):
        from agent_sandbox.domain.value_objects import MemoryLimit
        from agent_sandbox.exceptions import SandboxError, ErrorCode
        with pytest.raises(SandboxError) as exc_info:
            MemoryLimit(value=512, unit="tb")
        assert exc_info.value.code == ErrorCode.CONFIG_MALFORMED

    def test_memory_limit_is_frozen(self):
        from agent_sandbox.domain.value_objects import MemoryLimit
        m = MemoryLimit(value=512, unit="m")
        with pytest.raises((AttributeError, TypeError)):
            m.value = 1024  # type: ignore[misc]


class TestEnvVar:
    """EnvVar: frozen mapping of key-value environment variable pairs."""

    def test_env_var_can_be_created(self):
        from agent_sandbox.domain.value_objects import EnvVar
        e = EnvVar(key="FOO", value="bar")
        assert e.key == "FOO"
        assert e.value == "bar"

    def test_env_var_is_frozen(self):
        from agent_sandbox.domain.value_objects import EnvVar
        e = EnvVar(key="FOO", value="bar")
        with pytest.raises((AttributeError, TypeError)):
            e.key = "BAZ"  # type: ignore[misc]

    def test_env_var_allows_empty_value(self):
        from agent_sandbox.domain.value_objects import EnvVar
        e = EnvVar(key="EMPTY", value="")
        assert e.value == ""


class TestNoFrameworkImports:
    """value_objects.py must import no framework modules (domain layer)."""

    def _get_module_path(self) -> str:
        src_dir = os.path.join(os.path.dirname(__file__), "..", "..", "src")
        return os.path.join(src_dir, "agent_sandbox", "domain", "value_objects.py")

    def test_value_objects_file_exists(self):
        path = self._get_module_path()
        assert os.path.isfile(path), f"value_objects.py not found at {path}"

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
                        f"value_objects.py must not import '{top}' (domain layer)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"value_objects.py must not import from '{top}' (domain layer)"
                    )
