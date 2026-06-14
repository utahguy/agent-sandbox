"""Tests for FEAT-007: Sandbox facade.

TDD: tests written before implementation.

Covers:
  - Sandbox class is the public application facade
  - Sandbox(config).start() returns a ContainerHandle
  - Context manager: __enter__ returns handle, __exit__ calls stop()
  - Context manager calls stop() even on exception

Test criteria (from feature spec):
  5. Sandbox used as a context manager calls stop() on exit and on exception
  1. Sandbox(config).start() returns a ContainerHandle in running state
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_sandbox.domain.entities import ContainerHandle, ContainerState, ExecResult, SandboxConfig
from agent_sandbox.domain.value_objects import MemoryLimit, PortMapping, RuntimeKind, Volume
from agent_sandbox.exceptions import ErrorCode, SandboxError

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

SRC_DIR = Path(__file__).parent.parent / "src"
SANDBOX_MODULE = SRC_DIR / "agent_sandbox" / "facade.py"
INIT_MODULE = SRC_DIR / "agent_sandbox" / "__init__.py"


# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------


def _make_fake_handle(*, stopped_calls: list | None = None):
    """Return a fake ContainerHandlePort that records stop() calls."""

    class FakeHandle:
        def __init__(self):
            self._stopped_calls = stopped_calls if stopped_calls is not None else []
            self._stop_count = 0

        @property
        def container_id(self) -> str:
            return "fake-cid-1234"

        @property
        def image_tag(self) -> str:
            return "agent-sandbox:fake-tag"

        def exec(self, cmd, timeout=None) -> ExecResult:
            return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0, timed_out=False)

        def stop(self) -> None:
            self._stop_count += 1
            if self._stopped_calls is not None:
                self._stopped_calls.append(self._stop_count)

    return FakeHandle()


def _make_fake_start_use_case(*, handle=None, fail: bool = False):
    """Return a fake StartSandboxUseCase."""

    class FakeStartUseCase:
        def __init__(self, h):
            self._handle = h
            self.call_count = 0

        def execute(self, config, image_spec, containerfile_content):
            self.call_count += 1
            if fail:
                raise SandboxError(
                    "Start failed",
                    code=ErrorCode.CONTAINER_START_FAILED,
                )
            return self._handle

    return FakeStartUseCase(handle)


def _make_fake_stop_use_case(*, stop_calls: list | None = None):
    """Return a fake StopSandboxUseCase."""

    class FakeStopUseCase:
        def __init__(self):
            self._calls = stop_calls if stop_calls is not None else []

        def execute(self, handle) -> None:
            self._calls.append(handle)

    return FakeStopUseCase()


# ---------------------------------------------------------------------------
# 1. Sandbox importability
# ---------------------------------------------------------------------------


class TestSandboxImportable:
    """Sandbox must be importable from agent_sandbox package."""

    def test_importable_from_package(self):
        from agent_sandbox import Sandbox  # noqa: F401

        assert Sandbox is not None

    def test_instantiable_with_config(self):
        from agent_sandbox import Sandbox

        config = SandboxConfig()
        sb = Sandbox(config=config)
        assert sb is not None

    def test_has_start_method(self):
        from agent_sandbox import Sandbox

        assert hasattr(Sandbox, "start")
        assert callable(Sandbox.start)

    def test_has_stop_method(self):
        from agent_sandbox import Sandbox

        assert hasattr(Sandbox, "stop")
        assert callable(Sandbox.stop)

    def test_has_context_manager_enter(self):
        from agent_sandbox import Sandbox

        assert hasattr(Sandbox, "__enter__")
        assert callable(Sandbox.__enter__)

    def test_has_context_manager_exit(self):
        from agent_sandbox import Sandbox

        assert hasattr(Sandbox, "__exit__")
        assert callable(Sandbox.__exit__)


# ---------------------------------------------------------------------------
# 2. facade.py exists
# ---------------------------------------------------------------------------


class TestFacadeFileExists:
    """facade.py must exist in the package."""

    def test_facade_module_exists(self):
        assert SANDBOX_MODULE.is_file(), f"Missing: {SANDBOX_MODULE}"


# ---------------------------------------------------------------------------
# 3. Sandbox.start() returns ContainerHandlePort (Criterion 1)
# ---------------------------------------------------------------------------


class TestSandboxStart:
    """Criterion 1: Sandbox.start() returns a ContainerHandlePort."""

    def test_start_returns_handle(self):
        """Sandbox.start() must return something satisfying ContainerHandlePort."""
        from agent_sandbox import Sandbox
        from agent_sandbox.application.ports import ContainerHandlePort

        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case()

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        result = sb.start()

        assert result is not None
        assert isinstance(result, ContainerHandlePort)

    def test_start_returns_same_handle_as_stored(self):
        """Sandbox.start() must return the handle it received from the use case."""
        from agent_sandbox import Sandbox

        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case()

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        result = sb.start()
        assert result is handle

    def test_start_failure_propagates_sandbox_error(self):
        """SandboxError from start use case propagates to caller."""
        from agent_sandbox import Sandbox

        start_uc = _make_fake_start_use_case(handle=None, fail=True)
        stop_uc = _make_fake_stop_use_case()

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        with pytest.raises(SandboxError) as exc_info:
            sb.start()

        assert exc_info.value.code == ErrorCode.CONTAINER_START_FAILED

    def test_start_calls_start_use_case(self):
        """Sandbox.start() must delegate to the start use case."""
        from agent_sandbox import Sandbox

        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case()

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        sb.start()
        assert start_uc.call_count == 1, "start use case must be called exactly once"


# ---------------------------------------------------------------------------
# 4. Sandbox.stop()
# ---------------------------------------------------------------------------


class TestSandboxStop:
    """Sandbox.stop() must stop the running container (if started)."""

    def test_stop_calls_stop_use_case(self):
        """stop() must delegate to the stop use case."""
        from agent_sandbox import Sandbox

        stop_calls: list = []
        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case(stop_calls=stop_calls)

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        sb.start()
        sb.stop()

        assert len(stop_calls) >= 1, "stop use case must be called"

    def test_stop_before_start_is_noop(self):
        """stop() before start() must not raise (idempotent/safe)."""
        from agent_sandbox import Sandbox

        stop_calls: list = []
        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case(stop_calls=stop_calls)

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        # No start() call; stop() must not raise
        sb.stop()
        # No stop use case calls expected (nothing was started)
        assert len(stop_calls) == 0

    def test_stop_twice_is_safe(self):
        """stop() called twice must not raise."""
        from agent_sandbox import Sandbox

        stop_calls: list = []
        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case(stop_calls=stop_calls)

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        sb.start()
        sb.stop()
        sb.stop()  # must not raise


# ---------------------------------------------------------------------------
# 5. Sandbox as context manager (Criterion 5)
# ---------------------------------------------------------------------------


class TestSandboxContextManager:
    """Criterion 5: Sandbox as context manager calls stop() on exit and exception."""

    def test_context_manager_enter_starts_container(self):
        """__enter__ must start the container and return the handle."""
        from agent_sandbox import Sandbox
        from agent_sandbox.application.ports import ContainerHandlePort

        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case()

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        with sb as returned:
            assert isinstance(returned, ContainerHandlePort), (
                f"__enter__ must return a ContainerHandlePort, got: {type(returned)}"
            )

    def test_context_manager_exit_stops_container(self):
        """__exit__ must call stop() even on normal exit (Criterion 5)."""
        from agent_sandbox import Sandbox

        stop_calls: list = []
        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case(stop_calls=stop_calls)

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        with sb:
            pass  # normal exit

        assert len(stop_calls) >= 1, (
            "stop() must be called by __exit__ on normal exit"
        )

    def test_context_manager_exit_stops_on_exception(self):
        """__exit__ must call stop() when body raises an exception (Criterion 5)."""
        from agent_sandbox import Sandbox

        stop_calls: list = []
        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case(stop_calls=stop_calls)

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        try:
            with sb:
                raise ValueError("something went wrong inside the sandbox")
        except ValueError:
            pass  # expected

        assert len(stop_calls) >= 1, (
            "stop() must be called by __exit__ even when body raises"
        )

    def test_context_manager_does_not_suppress_exceptions(self):
        """__exit__ must NOT suppress exceptions raised inside the body."""
        from agent_sandbox import Sandbox

        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case()

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        with pytest.raises(RuntimeError, match="should propagate"):
            with sb:
                raise RuntimeError("should propagate")

    def test_context_manager_enter_returns_handle_not_sandbox(self):
        """__enter__ must return the handle (ContainerHandlePort), not the Sandbox."""
        from agent_sandbox import Sandbox
        from agent_sandbox.application.ports import ContainerHandlePort

        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case()

        sb = Sandbox(
            config=SandboxConfig(),
            _start_use_case=start_uc,
            _stop_use_case=stop_uc,
        )
        with sb as ctx:
            # ctx should be the handle, not the Sandbox object
            assert ctx is not sb, "__enter__ must return the handle, not self"
            assert isinstance(ctx, ContainerHandlePort)


# ---------------------------------------------------------------------------
# 6. __init__.py exports Sandbox
# ---------------------------------------------------------------------------


class TestPackageExportsSandbox:
    """agent_sandbox package must export Sandbox."""

    def test_sandbox_in_all(self):
        import agent_sandbox

        assert "Sandbox" in agent_sandbox.__all__

    def test_sandbox_is_functional_class(self):
        from agent_sandbox import Sandbox

        # Must be a class, not a placeholder stub
        config = SandboxConfig()
        handle = _make_fake_handle()
        start_uc = _make_fake_start_use_case(handle=handle)
        stop_uc = _make_fake_stop_use_case()

        sb = Sandbox(config=config, _start_use_case=start_uc, _stop_use_case=stop_uc)
        result = sb.start()
        assert result is handle


# ---------------------------------------------------------------------------
# 7. Facade import purity
# ---------------------------------------------------------------------------


class TestFacadeImportPurity:
    """facade.py must only use domain + application + infrastructure (no heavy frameworks)."""

    def _get_ast(self) -> ast.Module:
        path = SANDBOX_MODULE
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_framework_imports(self):
        """facade.py must not import click, fastapi, sqlalchemy, etc."""
        forbidden = {"click", "fastapi", "sqlalchemy", "flask", "django"}
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, (
                        f"facade.py must not import '{top}'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"facade.py must not import from '{top}'"
                    )
