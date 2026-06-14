"""Tests for FEAT-006: ImageSpec domain service.

TDD: tests written before implementation.

Covers:
  - ImageSpec: domain value object with deterministic tag computation
  - Tag is a pure function of (base_image, tooling_fingerprint)
  - Tag changes when either input changes
  - ImageSpec is domain-layer pure (no framework imports)

Test criteria (from feature spec):
  1. ImageSpec.tag is deterministic for identical inputs
  2. ImageSpec.tag changes when fingerprint changes
  3. ImageSpec is importable from domain layer
  4. Domain layer purity: zero framework imports
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

SRC_DIR = Path(__file__).parent.parent / "src"


def _domain_path(filename: str) -> Path:
    return SRC_DIR / "agent_sandbox" / "domain" / filename


# ---------------------------------------------------------------------------
# 1. Module / file existence
# ---------------------------------------------------------------------------


class TestModuleFilesExist:
    """Required source files must exist on disk."""

    def test_image_spec_module_exists(self):
        path = _domain_path("image_spec.py")
        assert path.is_file(), f"Missing: {path}"


# ---------------------------------------------------------------------------
# 2. ImageSpec importability
# ---------------------------------------------------------------------------


class TestImageSpecImportable:
    """ImageSpec must be importable from the domain layer."""

    def test_importable(self):
        from agent_sandbox.domain.image_spec import ImageSpec  # noqa: F401

        assert ImageSpec is not None

    def test_instantiable_with_base_image_and_fingerprint(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="abc123")
        assert spec is not None

    def test_has_base_image_attribute(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="abc123")
        assert spec.base_image == "ubuntu:22.04"

    def test_has_tooling_fingerprint_attribute(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="abc123")
        assert spec.tooling_fingerprint == "abc123"

    def test_has_tag_attribute(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="abc123")
        assert hasattr(spec, "tag")
        assert isinstance(spec.tag, str)

    def test_tag_is_non_empty_string(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="abc123")
        assert len(spec.tag) > 0


# ---------------------------------------------------------------------------
# 3. Deterministic tag computation (Criterion 1)
# ---------------------------------------------------------------------------


class TestImageSpecTagDeterminism:
    """ImageSpec.tag must be deterministic for identical inputs."""

    def test_tag_is_deterministic_for_same_inputs(self):
        """Criterion 1: identical inputs → identical tag."""
        from agent_sandbox.domain.image_spec import ImageSpec

        spec_a = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fingerprint-xyz")
        spec_b = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fingerprint-xyz")
        assert spec_a.tag == spec_b.tag

    def test_tag_multiple_instantiations_agree(self):
        """Creating the same spec many times always yields the same tag."""
        from agent_sandbox.domain.image_spec import ImageSpec

        tags = set(
            ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp1").tag
            for _ in range(10)
        )
        assert len(tags) == 1, f"Tag should be constant but got multiple values: {tags}"

    def test_tag_is_string(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        assert isinstance(spec.tag, str)


# ---------------------------------------------------------------------------
# 4. Tag changes when inputs change (Criterion 2)
# ---------------------------------------------------------------------------


class TestImageSpecTagSensitivity:
    """ImageSpec.tag must change when the fingerprint or base image changes."""

    def test_tag_changes_when_fingerprint_changes(self):
        """Criterion 2: different fingerprint → different tag (cache miss)."""
        from agent_sandbox.domain.image_spec import ImageSpec

        spec_a = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fingerprint-v1")
        spec_b = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fingerprint-v2")
        assert spec_a.tag != spec_b.tag

    def test_tag_changes_when_base_image_changes(self):
        """Different base_image → different tag even with same fingerprint."""
        from agent_sandbox.domain.image_spec import ImageSpec

        spec_a = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        spec_b = ImageSpec(base_image="ubuntu:24.04", tooling_fingerprint="fp")
        assert spec_a.tag != spec_b.tag

    def test_tag_changes_when_both_inputs_change(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        spec_a = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp-v1")
        spec_b = ImageSpec(base_image="debian:12", tooling_fingerprint="fp-v2")
        assert spec_a.tag != spec_b.tag

    def test_fingerprint_v1_different_from_v2(self):
        """Sanity check: two distinct fingerprints give distinct tags."""
        from agent_sandbox.domain.image_spec import ImageSpec

        tag1 = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="aaa").tag
        tag2 = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="bbb").tag
        assert tag1 != tag2


# ---------------------------------------------------------------------------
# 5. Tag format (stable, cache-friendly)
# ---------------------------------------------------------------------------


class TestImageSpecTagFormat:
    """ImageSpec.tag should produce a stable, runtime-friendly tag string."""

    def test_tag_contains_colon(self):
        """A valid Docker/Podman image tag includes a colon (name:version)."""
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        assert ":" in spec.tag, f"Expected colon in tag but got: {spec.tag!r}"

    def test_tag_does_not_contain_whitespace(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        assert " " not in spec.tag
        assert "\n" not in spec.tag
        assert "\t" not in spec.tag

    def test_tag_lower_case_only(self):
        """Docker/Podman tags must be lowercase."""
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        assert spec.tag == spec.tag.lower(), f"Tag must be lowercase: {spec.tag!r}"


# ---------------------------------------------------------------------------
# 6. Equality and hashability
# ---------------------------------------------------------------------------


class TestImageSpecEquality:
    """ImageSpec with same inputs should compare equal and be hashable."""

    def test_equal_specs_compare_equal(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        a = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        b = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        assert a == b

    def test_specs_with_different_fingerprint_not_equal(self):
        from agent_sandbox.domain.image_spec import ImageSpec

        a = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp1")
        b = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp2")
        assert a != b

    def test_spec_is_hashable(self):
        """ImageSpec should be usable as a dict key (frozen dataclass)."""
        from agent_sandbox.domain.image_spec import ImageSpec

        spec = ImageSpec(base_image="ubuntu:22.04", tooling_fingerprint="fp")
        # Should not raise
        d = {spec: "value"}
        assert d[spec] == "value"


# ---------------------------------------------------------------------------
# 7. Import purity: image_spec.py is domain-layer (zero framework imports)
# ---------------------------------------------------------------------------


class TestImageSpecImportPurity:
    """image_spec.py must import only stdlib — zero framework imports.

    Domain layer rule: no SQLAlchemy, FastAPI, subprocess, click, docker, podman.
    """

    def _get_ast(self) -> ast.Module:
        path = _domain_path("image_spec.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_subprocess_import(self):
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "subprocess", (
                        "image_spec.py (domain layer) must not import 'subprocess'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module.split(".")[0] != "subprocess", (
                        "image_spec.py must not import from 'subprocess'"
                    )

    def test_no_framework_imports(self):
        forbidden = {"click", "fastapi", "sqlalchemy", "flask", "django",
                     "docker", "podman", "subprocess"}
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, (
                        f"image_spec.py (domain) must not import '{top}'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"image_spec.py (domain) must not import from '{top}'"
                    )

    def test_no_infrastructure_layer_import(self):
        """Domain must not import from infrastructure or application layers."""
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "infrastructure" not in node.module, (
                        f"image_spec.py must not import from infrastructure: {node.module!r}"
                    )
                    assert "application" not in node.module, (
                        f"image_spec.py must not import from application: {node.module!r}"
                    )
