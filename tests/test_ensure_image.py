"""Tests for FEAT-006: EnsureImageUseCase and ContainerfileImageBuilder.

TDD: tests written before implementation.

Covers:
  - EnsureImageUseCase: orchestrates check-or-build using ImageBuilderPort
  - ContainerfileImageBuilder: implements ImageBuilderPort via RuntimePort
  - Containerfile: references mise + Claude CLI
  - Cache hit / miss logic
  - Build failure → SandboxError(IMAGE_BUILD_FAILED)
  - RuntimePort called with argument lists only (no shell strings)

Test criteria (from feature spec):
  1. EnsureImageUseCase skips build when image present (cache hit, no build call)
  2. EnsureImageUseCase triggers build when image absent (cache miss)
  3. Build failure from the runtime is translated to SandboxError(IMAGE_BUILD_FAILED)
  4. Containerfile references mise and the Claude CLI installation steps
  5. ImageBuilder calls the RuntimePort with argument lists only
  6. Application layer purity (no framework imports in use case)
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import pytest

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
# 1. Module / file existence
# ---------------------------------------------------------------------------


class TestModuleFilesExist:
    """Required source files must exist on disk."""

    def test_ensure_image_use_case_module_exists(self):
        path = _use_case_path("ensure_image.py")
        assert path.is_file(), f"Missing: {path}"

    def test_image_builder_module_exists(self):
        path = _infra_path("image_builder.py")
        assert path.is_file(), f"Missing: {path}"

    def test_containerfile_exists(self):
        path = INFRA_DIR / "Containerfile"
        assert path.is_file(), f"Missing: {path}"


# ---------------------------------------------------------------------------
# 2. EnsureImageUseCase importability
# ---------------------------------------------------------------------------


class TestEnsureImageUseCaseImportable:
    """EnsureImageUseCase must be importable from the application layer."""

    def test_importable(self):
        from agent_sandbox.application.use_cases.ensure_image import (  # noqa: F401
            EnsureImageUseCase,
        )

        assert EnsureImageUseCase is not None

    def test_instantiable_with_image_builder_port(self):
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase

        class FakeBuilder:
            def is_cached(self, image_tag: str) -> bool:
                return True

            def ensure_image(self, image_tag: str, containerfile_content: str) -> None:
                pass

        use_case = EnsureImageUseCase(image_builder=FakeBuilder())
        assert use_case is not None

    def test_has_execute_method(self):
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase

        assert hasattr(EnsureImageUseCase, "execute")
        assert callable(EnsureImageUseCase.execute)


# ---------------------------------------------------------------------------
# 3. Cache hit: EnsureImageUseCase skips build when image present (Criterion 1)
# ---------------------------------------------------------------------------


class TestEnsureImageCacheHit:
    """Criterion 1: cache hit → no build call."""

    def _make_fake_builder(self, *, cached: bool, build_called_flag: list):
        class FakeBuilder:
            def is_cached(self_inner, image_tag: str) -> bool:
                return cached

            def ensure_image(self_inner, image_tag: str, containerfile_content: str) -> None:
                build_called_flag.append(True)

        return FakeBuilder()

    def test_cache_hit_skips_build(self):
        """When is_cached returns True, ensure_image must NOT be called."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        build_called = []
        builder = self._make_fake_builder(cached=True, build_called_flag=build_called)
        use_case = EnsureImageUseCase(image_builder=builder)
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp-v1")

        use_case.execute(spec, containerfile_content="FROM ubuntu:22.04")

        assert len(build_called) == 0, "ensure_image must not be called on cache hit"

    def test_cache_hit_returns_none(self):
        """execute() should return None on cache hit (no observable side-effect)."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        class AlwaysCachedBuilder:
            def is_cached(self, image_tag: str) -> bool:
                return True

            def ensure_image(self, image_tag: str, containerfile_content: str) -> None:
                pass

        use_case = EnsureImageUseCase(image_builder=AlwaysCachedBuilder())
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        result = use_case.execute(spec, containerfile_content="FROM ubuntu:22.04")
        assert result is None

    def test_cache_hit_checks_correct_tag(self):
        """is_cached is called with spec.tag (the computed cache key)."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        checked_tags: list[str] = []

        class TrackingBuilder:
            def is_cached(self_inner, image_tag: str) -> bool:
                checked_tags.append(image_tag)
                return True

            def ensure_image(self_inner, image_tag: str, containerfile_content: str) -> None:
                pass

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp-xyz")
        use_case = EnsureImageUseCase(image_builder=TrackingBuilder())
        use_case.execute(spec, containerfile_content="FROM ubuntu:22.04")

        assert len(checked_tags) == 1
        assert checked_tags[0] == spec.tag, (
            f"is_cached must be called with spec.tag={spec.tag!r}, "
            f"got {checked_tags[0]!r}"
        )


# ---------------------------------------------------------------------------
# 4. Cache miss: EnsureImageUseCase triggers build when image absent (Criterion 2)
# ---------------------------------------------------------------------------


class TestEnsureImageCacheMiss:
    """Criterion 2: cache miss → build is triggered."""

    def test_cache_miss_triggers_build(self):
        """When is_cached returns False, ensure_image must be called."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        build_calls: list[tuple[str, str]] = []

        class NeverCachedBuilder:
            def is_cached(self, image_tag: str) -> bool:
                return False

            def ensure_image(self_inner, image_tag: str, containerfile_content: str) -> None:
                build_calls.append((image_tag, containerfile_content))

        use_case = EnsureImageUseCase(image_builder=NeverCachedBuilder())
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp-new")
        containerfile = "FROM ubuntu:22.04\nRUN echo hello"

        use_case.execute(spec, containerfile_content=containerfile)

        assert len(build_calls) == 1, "ensure_image must be called exactly once on cache miss"

    def test_cache_miss_passes_correct_tag_to_build(self):
        """ensure_image is called with the spec.tag as image_tag."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        build_tags: list[str] = []

        class NeverCachedBuilder:
            def is_cached(self, image_tag: str) -> bool:
                return False

            def ensure_image(self_inner, image_tag: str, containerfile_content: str) -> None:
                build_tags.append(image_tag)

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp-tag-check")
        use_case = EnsureImageUseCase(image_builder=NeverCachedBuilder())
        use_case.execute(spec, containerfile_content="FROM ubuntu:22.04")

        assert build_tags[0] == spec.tag, (
            f"ensure_image must receive spec.tag={spec.tag!r}, got {build_tags[0]!r}"
        )

    def test_cache_miss_passes_containerfile_content_to_build(self):
        """ensure_image receives the exact containerfile_content passed to execute()."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        received_content: list[str] = []

        class NeverCachedBuilder:
            def is_cached(self, image_tag: str) -> bool:
                return False

            def ensure_image(self_inner, image_tag: str, containerfile_content: str) -> None:
                received_content.append(containerfile_content)

        expected_content = "FROM ubuntu:22.04\nRUN apt-get install -y git"
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        use_case = EnsureImageUseCase(image_builder=NeverCachedBuilder())
        use_case.execute(spec, containerfile_content=expected_content)

        assert received_content[0] == expected_content


# ---------------------------------------------------------------------------
# 5. Build failure → SandboxError(IMAGE_BUILD_FAILED) (Criterion 3)
# ---------------------------------------------------------------------------


class TestBuildFailureTranslation:
    """Criterion 3: build failure translates to SandboxError(IMAGE_BUILD_FAILED)."""

    def test_build_failure_raises_sandbox_error(self):
        """SandboxError from ensure_image propagates (already typed correctly)."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        class FailingBuilder:
            def is_cached(self, image_tag: str) -> bool:
                return False

            def ensure_image(self, image_tag: str, containerfile_content: str) -> None:
                raise SandboxError(
                    "Build failed: Containerfile line 3 invalid",
                    code=ErrorCode.IMAGE_BUILD_FAILED,
                )

        use_case = EnsureImageUseCase(image_builder=FailingBuilder())
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")

        with pytest.raises(SandboxError) as exc_info:
            use_case.execute(spec, containerfile_content="FROM ubuntu:22.04\nRUN bad-cmd")

        assert exc_info.value.code == ErrorCode.IMAGE_BUILD_FAILED

    def test_build_failure_error_message_is_descriptive(self):
        """Build failure error carries a human-readable message."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        class FailingBuilder:
            def is_cached(self, image_tag: str) -> bool:
                return False

            def ensure_image(self, image_tag: str, containerfile_content: str) -> None:
                raise SandboxError(
                    "Image build failed: exit code 1 from RUN bad-command",
                    code=ErrorCode.IMAGE_BUILD_FAILED,
                )

        use_case = EnsureImageUseCase(image_builder=FailingBuilder())
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")

        with pytest.raises(SandboxError) as exc_info:
            use_case.execute(spec, containerfile_content="FROM ubuntu:22.04")

        msg = str(exc_info.value)
        assert len(msg) > 10, "Error message must be descriptive"

    def test_cache_hit_does_not_raise_on_build_failure(self):
        """If image is cached, build is never called even if it would fail."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec

        class CachedButBrokenBuilder:
            def is_cached(self, image_tag: str) -> bool:
                return True  # cached

            def ensure_image(self, image_tag: str, containerfile_content: str) -> None:
                raise SandboxError("Should not be called", code=ErrorCode.IMAGE_BUILD_FAILED)

        use_case = EnsureImageUseCase(image_builder=CachedButBrokenBuilder())
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")

        # Should not raise — cache hit skips build entirely
        use_case.execute(spec, containerfile_content="FROM ubuntu:22.04")


# ---------------------------------------------------------------------------
# 6. ContainerfileImageBuilder importability
# ---------------------------------------------------------------------------


class TestContainerfileImageBuilderImportable:
    """ContainerfileImageBuilder must be importable from infrastructure layer."""

    def test_importable(self):
        from agent_sandbox.infrastructure.image_builder import (  # noqa: F401
            ContainerfileImageBuilder,
        )

        assert ContainerfileImageBuilder is not None

    def test_instantiable_with_runtime_port(self):
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        class FakeRuntime:
            def detect(self):
                from agent_sandbox.domain.value_objects import RuntimeKind
                return RuntimeKind.DOCKER

            def run_cli(self, args: list, timeout=None) -> tuple:
                return (0, "", "")

        builder = ContainerfileImageBuilder(runtime_port=FakeRuntime())
        assert builder is not None

    def test_has_is_cached_method(self):
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        assert hasattr(ContainerfileImageBuilder, "is_cached")
        assert callable(ContainerfileImageBuilder.is_cached)

    def test_has_ensure_image_method(self):
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        assert hasattr(ContainerfileImageBuilder, "ensure_image")
        assert callable(ContainerfileImageBuilder.ensure_image)

    def test_satisfies_image_builder_port_protocol(self):
        """ContainerfileImageBuilder must be structurally compatible with ImageBuilderPort."""
        from agent_sandbox.application.ports import ImageBuilderPort
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        class FakeRuntime:
            def detect(self):
                from agent_sandbox.domain.value_objects import RuntimeKind
                return RuntimeKind.DOCKER

            def run_cli(self, args: list, timeout=None) -> tuple:
                return (0, "", "")

        builder = ContainerfileImageBuilder(runtime_port=FakeRuntime())
        assert isinstance(builder, ImageBuilderPort)


# ---------------------------------------------------------------------------
# 7. is_cached: checks image existence via RuntimePort with argument lists
#    (Criterion 5 — argument lists only)
# ---------------------------------------------------------------------------


class TestContainerfileImageBuilderIsCached:
    """is_cached must query image existence via RuntimePort with argument lists."""

    def _make_runtime(self, inspect_exit_code: int = 0):
        """Return a fake runtime that records all calls."""
        calls: list[list[str]] = []

        class FakeRuntime:
            def detect(self_inner):
                from agent_sandbox.domain.value_objects import RuntimeKind
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args: list[str], timeout=None) -> tuple[int, str, str]:
                assert isinstance(args, list), (
                    f"run_cli must receive a list, got {type(args).__name__!r}: {args!r}"
                )
                calls.append(list(args))
                return (inspect_exit_code, "", "")

        runtime = FakeRuntime()
        runtime._calls = calls  # type: ignore[attr-defined]
        return runtime

    def test_is_cached_returns_true_when_image_exists(self):
        """exit_code=0 from runtime means image exists → is_cached=True."""
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        runtime = self._make_runtime(inspect_exit_code=0)
        builder = ContainerfileImageBuilder(runtime_port=runtime)
        assert builder.is_cached("agent-sandbox:abc123") is True

    def test_is_cached_returns_false_when_image_absent(self):
        """Non-zero exit_code from runtime means image absent → is_cached=False."""
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        runtime = self._make_runtime(inspect_exit_code=1)
        builder = ContainerfileImageBuilder(runtime_port=runtime)
        assert builder.is_cached("agent-sandbox:abc123") is False

    def test_is_cached_calls_runtime_with_argument_list(self):
        """Criterion 5: runtime is called with a list, never a shell string."""
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        received_args: list = []

        class TrackingRuntime:
            def detect(self):
                from agent_sandbox.domain.value_objects import RuntimeKind
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args: list[str], timeout=None) -> tuple[int, str, str]:
                assert isinstance(args, list), (
                    f"run_cli must receive a list, got {type(args).__name__!r}"
                )
                received_args.extend(args)
                return (0, "", "")

        builder = ContainerfileImageBuilder(runtime_port=TrackingRuntime())
        builder.is_cached("agent-sandbox:sha256-xyz")

        assert len(received_args) > 0, "runtime.run_cli should have been called"

    def test_is_cached_returns_bool(self):
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        runtime = self._make_runtime(inspect_exit_code=0)
        builder = ContainerfileImageBuilder(runtime_port=runtime)
        result = builder.is_cached("agent-sandbox:abc")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 8. ensure_image: build calls use argument lists (Criterion 5)
# ---------------------------------------------------------------------------


class TestContainerfileImageBuilderEnsureImage:
    """ensure_image must build via RuntimePort argument lists only."""

    def _make_build_runtime(self, build_exit_code: int = 0):
        """Fake runtime for build operations."""
        calls: list[list[str]] = []

        class FakeRuntime:
            def detect(self_inner):
                from agent_sandbox.domain.value_objects import RuntimeKind
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args: list[str], timeout=None) -> tuple[int, str, str]:
                assert isinstance(args, list), (
                    f"run_cli must receive a list, got {type(args).__name__!r}"
                )
                calls.append(list(args))
                return (build_exit_code, "Build output", "")

        runtime = FakeRuntime()
        runtime._calls = calls  # type: ignore[attr-defined]
        return runtime

    def test_ensure_image_calls_runtime_with_arg_list(self):
        """Criterion 5: all runtime calls are argument lists."""
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        received_calls: list[list[str]] = []

        class TrackingRuntime:
            def detect(self):
                from agent_sandbox.domain.value_objects import RuntimeKind
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args: list[str], timeout=None) -> tuple[int, str, str]:
                assert isinstance(args, list), (
                    f"run_cli must receive a list, got {type(args).__name__!r}"
                )
                received_calls.append(list(args))
                return (0, "Successfully built", "")

        builder = ContainerfileImageBuilder(runtime_port=TrackingRuntime())
        builder.ensure_image("agent-sandbox:test123", "FROM ubuntu:22.04\nRUN echo hi")

        assert len(received_calls) > 0, "runtime.run_cli should have been called during build"
        for call in received_calls:
            assert isinstance(call, list), f"Call must be a list, got: {call!r}"

    def test_ensure_image_build_failure_raises_sandbox_error(self):
        """Criterion 3: build failure (non-zero exit) → SandboxError(IMAGE_BUILD_FAILED)."""
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        runtime = self._make_build_runtime(build_exit_code=1)
        builder = ContainerfileImageBuilder(runtime_port=runtime)

        with pytest.raises(SandboxError) as exc_info:
            builder.ensure_image(
                "agent-sandbox:fail-tag",
                "FROM ubuntu:22.04\nRUN exit 1",
            )

        assert exc_info.value.code == ErrorCode.IMAGE_BUILD_FAILED, (
            f"Expected IMAGE_BUILD_FAILED code, got: {exc_info.value.code!r}"
        )

    def test_ensure_image_build_failure_message_is_descriptive(self):
        """Build failure SandboxError carries a human-readable message."""
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        runtime = self._make_build_runtime(build_exit_code=1)
        builder = ContainerfileImageBuilder(runtime_port=runtime)

        with pytest.raises(SandboxError) as exc_info:
            builder.ensure_image("agent-sandbox:fail", "FROM ubuntu:22.04")

        msg = str(exc_info.value)
        assert len(msg) > 10, f"Error message must be descriptive, got: {msg!r}"

    def test_ensure_image_build_success_does_not_raise(self):
        """Successful build (exit_code=0) must not raise."""
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        runtime = self._make_build_runtime(build_exit_code=0)
        builder = ContainerfileImageBuilder(runtime_port=runtime)

        # Should not raise
        builder.ensure_image(
            "agent-sandbox:success-tag",
            "FROM ubuntu:22.04\nRUN echo built",
        )

    def test_ensure_image_does_not_use_shell_string(self):
        """Criterion 5: no shell=True or shell-string calls to runtime."""
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        received_as_str: list[str] = []

        class ShellDetectRuntime:
            def detect(self):
                from agent_sandbox.domain.value_objects import RuntimeKind
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args, timeout=None) -> tuple[int, str, str]:
                if isinstance(args, str):
                    received_as_str.append(args)
                return (0, "", "")

        builder = ContainerfileImageBuilder(runtime_port=ShellDetectRuntime())
        builder.ensure_image("agent-sandbox:test", "FROM ubuntu:22.04")

        assert len(received_as_str) == 0, (
            f"runtime.run_cli was called with shell strings: {received_as_str}"
        )


# ---------------------------------------------------------------------------
# 9. Containerfile content: references mise and Claude CLI (Criterion 4)
# ---------------------------------------------------------------------------


class TestContainerfileContent:
    """Criterion 4: Containerfile must reference mise and Claude CLI."""

    def _read_containerfile(self) -> str:
        path = INFRA_DIR / "Containerfile"
        return path.read_text()

    def test_containerfile_is_non_empty(self):
        content = self._read_containerfile()
        assert len(content.strip()) > 0

    def test_containerfile_has_from_directive(self):
        """Containerfile must start with a FROM directive."""
        content = self._read_containerfile()
        assert any(
            line.strip().upper().startswith("FROM")
            for line in content.splitlines()
        ), "Containerfile must have a FROM directive"

    def test_containerfile_references_mise(self):
        """Criterion 4: Containerfile must mention mise (dev tools manager)."""
        content = self._read_containerfile().lower()
        assert "mise" in content, (
            "Containerfile must reference mise (dev tools manager)"
        )

    def test_containerfile_references_claude(self):
        """Criterion 4: Containerfile must reference Claude CLI installation."""
        content = self._read_containerfile().lower()
        assert "claude" in content, (
            "Containerfile must reference Claude CLI installation"
        )

    def test_containerfile_references_curl_or_install_command(self):
        """Containerfile must include an install command (curl, npm, apt, etc.)."""
        content = self._read_containerfile().lower()
        has_install = any(
            kw in content
            for kw in ("curl", "apt-get", "npm", "pip", "wget", "install")
        )
        assert has_install, "Containerfile must include installation commands"

    def test_containerfile_has_run_directives(self):
        """Containerfile must have at least one RUN directive."""
        content = self._read_containerfile()
        run_lines = [
            line for line in content.splitlines()
            if line.strip().upper().startswith("RUN")
        ]
        assert len(run_lines) >= 1, "Containerfile must have at least one RUN directive"


# ---------------------------------------------------------------------------
# 10. Application layer import purity: ensure_image.py
# ---------------------------------------------------------------------------


class TestEnsureImageUseCaseImportPurity:
    """ensure_image.py must import only domain + ports + stdlib (application layer).

    No subprocess, click, docker, podman, or infrastructure module imports.
    """

    def _get_ast(self) -> ast.Module:
        path = _use_case_path("ensure_image.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_subprocess_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "subprocess", (
                        "ensure_image.py (application layer) must not import 'subprocess'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "subprocess", (
                        "ensure_image.py must not import from 'subprocess'"
                    )

    def test_no_infrastructure_import(self):
        tree = self._get_ast()
        forbidden_infra_modules = {
            "subprocess", "click", "fastapi", "sqlalchemy",
            "flask", "django", "docker", "podman",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden_infra_modules, (
                        f"ensure_image.py must not import '{top}' (application layer)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden_infra_modules, (
                        f"ensure_image.py must not import from '{top}'"
                    )

    def test_no_infrastructure_layer_import(self):
        """Application use case must not import from infrastructure layer."""
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "infrastructure" in node.module:
                    pytest.fail(
                        f"ensure_image.py must not import from infrastructure: {node.module!r}"
                    )


# ---------------------------------------------------------------------------
# 11. Infrastructure layer import purity: image_builder.py
# ---------------------------------------------------------------------------


class TestImageBuilderImportPurity:
    """image_builder.py (infrastructure) may use subprocess, domain, ports, stdlib.

    Must not import heavy frameworks (fastapi, sqlalchemy, click, etc.).
    """

    def _get_ast(self) -> ast.Module:
        path = _infra_path("image_builder.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_framework_imports(self):
        """Infrastructure must not import heavy web/ORM frameworks."""
        forbidden = {"click", "fastapi", "sqlalchemy", "flask", "django"}
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, (
                        f"image_builder.py must not import '{top}'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"image_builder.py must not import from '{top}'"
                    )


# ---------------------------------------------------------------------------
# 12. Integration: EnsureImageUseCase + ContainerfileImageBuilder + fake runtime
# ---------------------------------------------------------------------------


class TestEnsureImageIntegration:
    """End-to-end: EnsureImageUseCase + ContainerfileImageBuilder with mock runtime."""

    def _make_fake_runtime(self, *, inspect_exit: int = 0, build_exit: int = 0):
        """
        Fake runtime that controls inspect (is_cached) and build (ensure_image) results.
        inspect calls: ["inspect", tag] — exit 0 means cached, 1 means not cached
        build calls: ["build", ...] — exit 0 means success, non-zero means failure
        """
        calls: list[list[str]] = []

        class FakeRuntime:
            def detect(self_inner):
                from agent_sandbox.domain.value_objects import RuntimeKind
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args: list[str], timeout=None) -> tuple[int, str, str]:
                assert isinstance(args, list), (
                    f"Must receive list, got {type(args).__name__!r}"
                )
                calls.append(list(args))
                # Determine if this is an inspect or build call
                if len(args) >= 1 and any(kw in args for kw in ("inspect", "image")):
                    return (inspect_exit, "", "")
                elif len(args) >= 1 and "build" in args:
                    return (build_exit, "build output", "build stderr" if build_exit != 0 else "")
                return (0, "", "")

        runtime = FakeRuntime()
        runtime._calls = calls  # type: ignore[attr-defined]
        return runtime

    def test_integration_cache_hit_no_build_calls(self):
        """Integration: cache hit → ContainerfileImageBuilder skips build."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        runtime = self._make_fake_runtime(inspect_exit=0)  # image is cached
        builder = ContainerfileImageBuilder(runtime_port=runtime)
        use_case = EnsureImageUseCase(image_builder=builder)
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")

        use_case.execute(spec, containerfile_content="FROM ubuntu:22.04")

        # Verify no build command was run
        build_calls = [c for c in runtime._calls if "build" in c]
        assert len(build_calls) == 0, f"No build should happen on cache hit, got: {build_calls}"

    def test_integration_cache_miss_triggers_build(self):
        """Integration: cache miss → ContainerfileImageBuilder triggers build."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        runtime = self._make_fake_runtime(inspect_exit=1, build_exit=0)  # not cached
        builder = ContainerfileImageBuilder(runtime_port=runtime)
        use_case = EnsureImageUseCase(image_builder=builder)
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp-new")

        use_case.execute(spec, containerfile_content="FROM ubuntu:22.04")

        # Verify a build command was run
        build_calls = [c for c in runtime._calls if "build" in c]
        assert len(build_calls) >= 1, "A build call should happen on cache miss"

    def test_integration_all_runtime_calls_are_lists(self):
        """Criterion 5 (integration): all calls to runtime.run_cli are arg lists."""
        from agent_sandbox.application.use_cases.ensure_image import EnsureImageUseCase
        from agent_sandbox.domain.image_spec import ImageSpec
        from agent_sandbox.infrastructure.image_builder import ContainerfileImageBuilder

        string_calls: list[str] = []

        class StrictRuntime:
            def detect(self):
                from agent_sandbox.domain.value_objects import RuntimeKind
                return RuntimeKind.DOCKER

            def run_cli(self_inner, args, timeout=None) -> tuple[int, str, str]:
                if isinstance(args, str):
                    string_calls.append(args)
                return (1, "", "")  # not cached

        builder = ContainerfileImageBuilder(runtime_port=StrictRuntime())
        use_case = EnsureImageUseCase(image_builder=builder)
        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp-strict")

        try:
            use_case.execute(spec, containerfile_content="FROM ubuntu:22.04")
        except SandboxError:
            pass  # build might fail, that's ok for this test

        assert len(string_calls) == 0, (
            f"runtime.run_cli was called with shell strings: {string_calls}"
        )
