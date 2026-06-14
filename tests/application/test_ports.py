"""Tests for application ports (FEAT-003).

TDD: tests written before implementation.
Application layer must import only domain types and stdlib (no subprocess,
click, docker, podman, or any infrastructure framework).

Port contracts:
  - ConfigSourcePort   (read raw config text)
  - RuntimePort        (detect / run container CLI)
  - ImageBuilderPort   (build / check image cache)
  - ContainerHandlePort (abstract running container)
  - ContainerPort      (start / exec / stop)
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Union
import pytest


# ---------------------------------------------------------------------------
# Helpers to locate the source file
# ---------------------------------------------------------------------------

def _ports_path() -> str:
    src_dir = os.path.join(os.path.dirname(__file__), "..", "..", "src")
    return os.path.join(src_dir, "agent_sandbox", "application", "ports.py")


def _app_init_path() -> str:
    src_dir = os.path.join(os.path.dirname(__file__), "..", "..", "src")
    return os.path.join(src_dir, "agent_sandbox", "application", "__init__.py")


# ---------------------------------------------------------------------------
# Fake (in-memory) implementations used to verify structural compatibility
# ---------------------------------------------------------------------------

class FakeConfigSource:
    """Fake implementation that satisfies ConfigSourcePort."""

    def read_text(self) -> str:
        return "volumes:\n  - /src:/workspace:rw\n"


class FakeRuntime:
    """Fake implementation that satisfies RuntimePort."""

    def detect(self):
        from agent_sandbox.domain.value_objects import RuntimeKind
        return RuntimeKind.DOCKER

    def run_cli(self, args: list, timeout=None) -> tuple:
        return (0, "ok", "")


class FakeImageBuilder:
    """Fake implementation that satisfies ImageBuilderPort."""

    def is_cached(self, image_tag: str) -> bool:
        return False

    def ensure_image(self, image_tag: str, containerfile_content: str) -> None:
        pass  # no-op


class FakeContainerHandle:
    """Fake implementation that satisfies ContainerHandlePort."""

    def __init__(self) -> None:
        self.container_id = "fake-container-abc123"
        self.image_tag = "sandbox:latest"

    def exec(self, cmd, timeout=None):
        from agent_sandbox.domain.entities import ExecResult
        return ExecResult(
            exit_code=0,
            stdout="hello",
            stderr="",
            duration_ms=10,
            timed_out=False,
        )

    def stop(self) -> None:
        pass


class FakeContainerPort:
    """Fake implementation that satisfies ContainerPort."""

    def start(self, config, image_tag: str):
        return FakeContainerHandle()

    def exec(self, handle, cmd, timeout=None):
        from agent_sandbox.domain.entities import ExecResult
        return ExecResult(
            exit_code=0,
            stdout="result",
            stderr="",
            duration_ms=5,
            timed_out=False,
        )

    def stop(self, handle) -> None:
        pass


# ---------------------------------------------------------------------------
# Module / file existence
# ---------------------------------------------------------------------------

class TestApplicationModuleExists:
    """application/__init__.py and ports.py must exist."""

    def test_application_init_exists(self):
        path = _app_init_path()
        assert os.path.isfile(path), f"application/__init__.py not found at {path}"

    def test_ports_module_exists(self):
        path = _ports_path()
        assert os.path.isfile(path), f"ports.py not found at {path}"

    def test_ports_module_importable(self):
        from agent_sandbox.application import ports  # noqa: F401
        assert ports is not None

    def test_application_package_importable(self):
        import agent_sandbox.application  # noqa: F401


# ---------------------------------------------------------------------------
# ConfigSourcePort
# ---------------------------------------------------------------------------

class TestConfigSourcePort:
    """ConfigSourcePort: Protocol with read_text() -> str."""

    def test_config_source_port_importable(self):
        from agent_sandbox.application.ports import ConfigSourcePort
        assert ConfigSourcePort is not None

    def test_config_source_port_is_protocol(self):
        import typing
        from agent_sandbox.application.ports import ConfigSourcePort
        # Protocols have _is_protocol = True
        assert getattr(ConfigSourcePort, "_is_protocol", False) is True

    def test_config_source_port_has_read_text_method(self):
        from agent_sandbox.application.ports import ConfigSourcePort
        # The protocol must declare read_text as an abstract method
        assert hasattr(ConfigSourcePort, "read_text")

    def test_fake_config_source_satisfies_protocol(self):
        """FakeConfigSource has read_text() -> str; must be structurally compatible."""
        from agent_sandbox.application.ports import ConfigSourcePort
        fake = FakeConfigSource()
        # Structural check: should have read_text
        assert hasattr(fake, "read_text")
        result = fake.read_text()
        assert isinstance(result, str)

    def test_config_source_read_text_returns_str(self):
        fake = FakeConfigSource()
        assert isinstance(fake.read_text(), str)


# ---------------------------------------------------------------------------
# RuntimePort
# ---------------------------------------------------------------------------

class TestRuntimePort:
    """RuntimePort: Protocol with detect() -> RuntimeKind and run_cli(...)."""

    def test_runtime_port_importable(self):
        from agent_sandbox.application.ports import RuntimePort
        assert RuntimePort is not None

    def test_runtime_port_is_protocol(self):
        from agent_sandbox.application.ports import RuntimePort
        assert getattr(RuntimePort, "_is_protocol", False) is True

    def test_runtime_port_has_detect_method(self):
        from agent_sandbox.application.ports import RuntimePort
        assert hasattr(RuntimePort, "detect")

    def test_runtime_port_has_run_cli_method(self):
        from agent_sandbox.application.ports import RuntimePort
        assert hasattr(RuntimePort, "run_cli")

    def test_fake_runtime_detect_returns_runtime_kind(self):
        from agent_sandbox.domain.value_objects import RuntimeKind
        fake = FakeRuntime()
        result = fake.detect()
        assert isinstance(result, RuntimeKind)

    def test_fake_runtime_run_cli_returns_tuple(self):
        fake = FakeRuntime()
        result = fake.run_cli(["version"])
        assert isinstance(result, tuple)
        assert len(result) == 3
        exit_code, stdout, stderr = result
        assert isinstance(exit_code, int)
        assert isinstance(stdout, str)
        assert isinstance(stderr, str)

    def test_fake_runtime_satisfies_protocol(self):
        """FakeRuntime must be structurally compatible with RuntimePort."""
        fake = FakeRuntime()
        assert hasattr(fake, "detect")
        assert hasattr(fake, "run_cli")


# ---------------------------------------------------------------------------
# ImageBuilderPort
# ---------------------------------------------------------------------------

class TestImageBuilderPort:
    """ImageBuilderPort: Protocol with is_cached(...) and ensure_image(...)."""

    def test_image_builder_port_importable(self):
        from agent_sandbox.application.ports import ImageBuilderPort
        assert ImageBuilderPort is not None

    def test_image_builder_port_is_protocol(self):
        from agent_sandbox.application.ports import ImageBuilderPort
        assert getattr(ImageBuilderPort, "_is_protocol", False) is True

    def test_image_builder_port_has_is_cached_method(self):
        from agent_sandbox.application.ports import ImageBuilderPort
        assert hasattr(ImageBuilderPort, "is_cached")

    def test_image_builder_port_has_ensure_image_method(self):
        from agent_sandbox.application.ports import ImageBuilderPort
        assert hasattr(ImageBuilderPort, "ensure_image")

    def test_fake_image_builder_is_cached_returns_bool(self):
        fake = FakeImageBuilder()
        result = fake.is_cached("sandbox:abc123")
        assert isinstance(result, bool)

    def test_fake_image_builder_ensure_image_no_return(self):
        fake = FakeImageBuilder()
        result = fake.ensure_image("sandbox:abc123", "FROM ubuntu:22.04\n")
        assert result is None

    def test_fake_image_builder_satisfies_protocol(self):
        fake = FakeImageBuilder()
        assert hasattr(fake, "is_cached")
        assert hasattr(fake, "ensure_image")


# ---------------------------------------------------------------------------
# ContainerHandlePort
# ---------------------------------------------------------------------------

class TestContainerHandlePort:
    """ContainerHandlePort: Protocol for an active container (exec/stop)."""

    def test_container_handle_port_importable(self):
        from agent_sandbox.application.ports import ContainerHandlePort
        assert ContainerHandlePort is not None

    def test_container_handle_port_is_protocol(self):
        from agent_sandbox.application.ports import ContainerHandlePort
        assert getattr(ContainerHandlePort, "_is_protocol", False) is True

    def test_container_handle_port_has_container_id_attr(self):
        from agent_sandbox.application.ports import ContainerHandlePort
        assert hasattr(ContainerHandlePort, "container_id")

    def test_container_handle_port_has_image_tag_attr(self):
        from agent_sandbox.application.ports import ContainerHandlePort
        assert hasattr(ContainerHandlePort, "image_tag")

    def test_container_handle_port_has_exec_method(self):
        from agent_sandbox.application.ports import ContainerHandlePort
        assert hasattr(ContainerHandlePort, "exec")

    def test_container_handle_port_has_stop_method(self):
        from agent_sandbox.application.ports import ContainerHandlePort
        assert hasattr(ContainerHandlePort, "stop")

    def test_fake_container_handle_exec_returns_exec_result(self):
        from agent_sandbox.domain.entities import ExecResult
        fake = FakeContainerHandle()
        result = fake.exec("echo hello")
        assert isinstance(result, ExecResult)
        assert result.exit_code == 0
        assert result.stdout == "hello"

    def test_fake_container_handle_stop_is_callable(self):
        fake = FakeContainerHandle()
        fake.stop()  # should not raise

    def test_fake_container_handle_has_container_id(self):
        fake = FakeContainerHandle()
        assert isinstance(fake.container_id, str)

    def test_fake_container_handle_has_image_tag(self):
        fake = FakeContainerHandle()
        assert isinstance(fake.image_tag, str)

    def test_fake_container_handle_satisfies_protocol(self):
        fake = FakeContainerHandle()
        assert hasattr(fake, "container_id")
        assert hasattr(fake, "image_tag")
        assert hasattr(fake, "exec")
        assert hasattr(fake, "stop")


# ---------------------------------------------------------------------------
# ContainerPort
# ---------------------------------------------------------------------------

class TestContainerPort:
    """ContainerPort: Protocol with start/exec/stop."""

    def test_container_port_importable(self):
        from agent_sandbox.application.ports import ContainerPort
        assert ContainerPort is not None

    def test_container_port_is_protocol(self):
        from agent_sandbox.application.ports import ContainerPort
        assert getattr(ContainerPort, "_is_protocol", False) is True

    def test_container_port_has_start_method(self):
        from agent_sandbox.application.ports import ContainerPort
        assert hasattr(ContainerPort, "start")

    def test_container_port_has_exec_method(self):
        from agent_sandbox.application.ports import ContainerPort
        assert hasattr(ContainerPort, "exec")

    def test_container_port_has_stop_method(self):
        from agent_sandbox.application.ports import ContainerPort
        assert hasattr(ContainerPort, "stop")

    def test_fake_container_port_start_returns_handle(self):
        from agent_sandbox.domain.entities import SandboxConfig
        fake = FakeContainerPort()
        config = SandboxConfig()
        handle = fake.start(config, "sandbox:latest")
        assert handle is not None
        assert hasattr(handle, "exec")
        assert hasattr(handle, "stop")

    def test_fake_container_port_exec_returns_exec_result(self):
        from agent_sandbox.domain.entities import ExecResult, SandboxConfig
        fake = FakeContainerPort()
        config = SandboxConfig()
        handle = fake.start(config, "sandbox:latest")
        result = fake.exec(handle, "ls /workspace")
        assert isinstance(result, ExecResult)

    def test_fake_container_port_stop_is_callable(self):
        from agent_sandbox.domain.entities import SandboxConfig
        fake = FakeContainerPort()
        config = SandboxConfig()
        handle = fake.start(config, "sandbox:latest")
        fake.stop(handle)  # should not raise

    def test_fake_container_port_satisfies_protocol(self):
        fake = FakeContainerPort()
        assert hasattr(fake, "start")
        assert hasattr(fake, "exec")
        assert hasattr(fake, "stop")


# ---------------------------------------------------------------------------
# Import purity: ports.py must only import from domain and stdlib
# ---------------------------------------------------------------------------

class TestPortsImportPurity:
    """ports.py must only import domain types and stdlib (no infra imports)."""

    def _get_ast(self):
        with open(_ports_path()) as f:
            return ast.parse(f.read())

    def test_ports_file_exists(self):
        assert os.path.isfile(_ports_path())

    def test_no_subprocess_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "subprocess", (
                        "ports.py must not import 'subprocess' (application layer)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "subprocess", (
                        "ports.py must not import from 'subprocess'"
                    )

    def test_no_click_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "click", (
                        "ports.py must not import 'click' (application layer)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "click", (
                        "ports.py must not import from 'click'"
                    )

    def test_no_docker_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "docker", (
                        "ports.py must not import 'docker'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "docker", (
                        "ports.py must not import from 'docker'"
                    )

    def test_no_sqlalchemy_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "sqlalchemy", (
                        "ports.py must not import 'sqlalchemy'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "sqlalchemy", (
                        "ports.py must not import from 'sqlalchemy'"
                    )

    def test_no_fastapi_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "fastapi", (
                        "ports.py must not import 'fastapi'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "fastapi", (
                        "ports.py must not import from 'fastapi'"
                    )

    def test_forbidden_infra_imports_absent(self):
        """Generic check: no forbidden infrastructure module imports."""
        forbidden = {"subprocess", "click", "fastapi", "sqlalchemy", "flask",
                     "django", "docker", "podman"}
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, (
                        f"ports.py must not import '{top}' (application layer)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"ports.py must not import from '{top}'"
                    )

    def test_ports_imports_only_domain_and_typing(self):
        """All non-stdlib, non-builtin imports must be from agent_sandbox.domain."""
        tree = self._get_ast()
        allowed_top_level = {
            "typing",
            "typing_extensions",
            "agent_sandbox",
            "__future__",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    # If it's agent_sandbox, it must only come from .domain
                    if top == "agent_sandbox":
                        parts = node.module.split(".")
                        # Allow agent_sandbox.domain.* only
                        assert len(parts) >= 2 and parts[1] == "domain", (
                            f"ports.py may only import from agent_sandbox.domain, "
                            f"not from '{node.module}'"
                        )
                    else:
                        assert top in allowed_top_level or top.startswith("_"), (
                            f"ports.py should not import from '{node.module}'"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top in allowed_top_level or top.startswith("_"), (
                        f"ports.py should not import '{alias.name}'"
                    )


# ---------------------------------------------------------------------------
# All five port names are exported from the module
# ---------------------------------------------------------------------------

class TestPortsPublicAPI:
    """All five port Protocol classes must be importable from ports module."""

    def test_all_ports_in_module(self):
        from agent_sandbox.application import ports
        expected = [
            "ConfigSourcePort",
            "RuntimePort",
            "ImageBuilderPort",
            "ContainerHandlePort",
            "ContainerPort",
        ]
        for name in expected:
            assert hasattr(ports, name), f"ports.{name} is missing"

    def test_config_source_port_is_class(self):
        from agent_sandbox.application.ports import ConfigSourcePort
        assert isinstance(ConfigSourcePort, type)

    def test_runtime_port_is_class(self):
        from agent_sandbox.application.ports import RuntimePort
        assert isinstance(RuntimePort, type)

    def test_image_builder_port_is_class(self):
        from agent_sandbox.application.ports import ImageBuilderPort
        assert isinstance(ImageBuilderPort, type)

    def test_container_handle_port_is_class(self):
        from agent_sandbox.application.ports import ContainerHandlePort
        assert isinstance(ContainerHandlePort, type)

    def test_container_port_is_class(self):
        from agent_sandbox.application.ports import ContainerPort
        assert isinstance(ContainerPort, type)
