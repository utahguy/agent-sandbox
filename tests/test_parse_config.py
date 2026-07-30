"""Tests for FEAT-004: Config parsing.

TDD: tests are written before implementation.

Covers:
  - ParseConfigUseCase: maps directives into typed SandboxConfig
  - FileConfigSource: reads .agent-sandbox / .claude-sandbox files
  - SandboxConfig.from_file: facade wiring use case to FileConfigSource

Test criteria (from feature spec):
  1. A representative valid config parses into a SandboxConfig with correct
     volumes/ports/env/mise/memory/runtime
  2. SandboxConfig.from_file accepts both .agent-sandbox and .claude-sandbox
  3. Malformed directive raises SandboxError with CONFIG_MALFORMED and
     human-readable message
  4. Missing file raises SandboxError (not raw FileNotFoundError)
  5. Volume host_path with traversal is rejected
  6. source_filename/config_path provenance is recorded
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALID_CONFIG = FIXTURES_DIR / "valid.agent-sandbox"
MALFORMED_CONFIG = FIXTURES_DIR / "malformed.agent-sandbox"

SRC_DIR = Path(__file__).parent.parent / "src"


def _infra_path(filename: str) -> Path:
    return SRC_DIR / "agent_sandbox" / "infrastructure" / filename


def _use_case_path(filename: str) -> Path:
    return SRC_DIR / "agent_sandbox" / "application" / "use_cases" / filename


# ---------------------------------------------------------------------------
# 1. Fixture files exist
# ---------------------------------------------------------------------------

class TestFixtureFilesExist:
    """Fixture files referenced by tests must exist on disk."""

    def test_valid_config_fixture_exists(self):
        assert VALID_CONFIG.is_file(), f"Missing fixture: {VALID_CONFIG}"

    def test_malformed_config_fixture_exists(self):
        assert MALFORMED_CONFIG.is_file(), f"Missing fixture: {MALFORMED_CONFIG}"


# ---------------------------------------------------------------------------
# 2. FileConfigSource (infrastructure layer)
# ---------------------------------------------------------------------------

class TestFileConfigSourceExists:
    """infrastructure/file_config_source.py must exist and be importable."""

    def test_infrastructure_init_exists(self):
        path = _infra_path("__init__.py")
        assert path.is_file(), f"Missing: {path}"

    def test_file_config_source_module_exists(self):
        path = _infra_path("file_config_source.py")
        assert path.is_file(), f"Missing: {path}"

    def test_file_config_source_importable(self):
        from agent_sandbox.infrastructure.file_config_source import FileConfigSource  # noqa: F401
        assert FileConfigSource is not None


class TestFileConfigSource:
    """FileConfigSource reads raw config text from a file path."""

    def test_read_text_returns_string(self):
        from agent_sandbox.infrastructure.file_config_source import FileConfigSource
        src = FileConfigSource(VALID_CONFIG)
        text = src.read_text()
        assert isinstance(text, str)
        assert len(text) > 0

    def test_read_text_contains_config_directives(self):
        from agent_sandbox.infrastructure.file_config_source import FileConfigSource
        src = FileConfigSource(VALID_CONFIG)
        text = src.read_text()
        assert "volume" in text

    def test_missing_file_raises_sandbox_error(self):
        from agent_sandbox.infrastructure.file_config_source import FileConfigSource
        from agent_sandbox.exceptions import SandboxError
        src = FileConfigSource(Path("/nonexistent/.agent-sandbox"))
        with pytest.raises(SandboxError) as exc_info:
            src.read_text()
        assert exc_info.value.code == "CONFIG_MALFORMED" or "not found" in str(exc_info.value).lower() or "no such" in str(exc_info.value).lower() or exc_info.value.code != ""

    def test_missing_file_does_not_raise_raw_file_not_found_error(self):
        """FileNotFoundError must be translated to SandboxError."""
        from agent_sandbox.infrastructure.file_config_source import FileConfigSource
        from agent_sandbox.exceptions import SandboxError
        src = FileConfigSource(Path("/nonexistent/.agent-sandbox"))
        with pytest.raises(SandboxError):
            src.read_text()
        # If we reach here without FileNotFoundError, the test passes

    def test_satisfies_config_source_port(self):
        """FileConfigSource must be structurally compatible with ConfigSourcePort."""
        from agent_sandbox.infrastructure.file_config_source import FileConfigSource
        src = FileConfigSource(VALID_CONFIG)
        assert hasattr(src, "read_text")
        assert callable(src.read_text)

    def test_accepts_path_object(self):
        from agent_sandbox.infrastructure.file_config_source import FileConfigSource
        src = FileConfigSource(VALID_CONFIG)
        result = src.read_text()
        assert isinstance(result, str)

    def test_accepts_string_path(self):
        from agent_sandbox.infrastructure.file_config_source import FileConfigSource
        src = FileConfigSource(str(VALID_CONFIG))
        result = src.read_text()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 3. ParseConfigUseCase (application layer)
# ---------------------------------------------------------------------------

class TestParseConfigUseCaseExists:
    """application/use_cases/parse_config.py must exist and be importable."""

    def test_use_cases_directory_exists(self):
        path = _use_case_path("__init__.py")
        assert path.is_file() or path.parent.is_dir(), \
            f"use_cases directory missing: {path.parent}"

    def test_parse_config_module_exists(self):
        path = _use_case_path("parse_config.py")
        assert path.is_file(), f"Missing: {path}"

    def test_parse_config_use_case_importable(self):
        from agent_sandbox.application.use_cases.parse_config import ParseConfigUseCase  # noqa: F401
        assert ParseConfigUseCase is not None


class TestParseConfigUseCaseParsesValidConfig:
    """ParseConfigUseCase correctly maps all directive types."""

    def _parse(self, text: str):
        from agent_sandbox.application.use_cases.parse_config import ParseConfigUseCase
        use_case = ParseConfigUseCase()
        return use_case.execute(text)

    def test_execute_returns_sandbox_config(self):
        from agent_sandbox.domain.entities import SandboxConfig
        result = self._parse("# empty config\n")
        assert isinstance(result, SandboxConfig)

    def test_valid_fixture_parses_without_error(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        assert result is not None

    # --- Volumes ---

    def test_volume_count(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        assert len(result.volumes) == 2

    def test_volume_host_path(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        host_paths = [v.host_path for v in result.volumes]
        assert "/src" in host_paths

    def test_volume_container_path(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        container_paths = [v.container_path for v in result.volumes]
        assert "/workspace" in container_paths

    def test_volume_mode_rw(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        rw_vols = [v for v in result.volumes if v.host_path == "/src"]
        assert rw_vols[0].mode == "rw"

    def test_volume_mode_ro(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        ro_vols = [v for v in result.volumes if v.host_path == "/data"]
        assert ro_vols[0].mode == "ro"

    def test_volume_default_mode_is_rw(self):
        result = self._parse("volume /src:/workspace\n")
        assert result.volumes[0].mode == "rw"

    # --- Ports ---

    def test_port_count(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        assert len(result.ports) == 2

    def test_port_host_port(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        host_ports = [p.host_port for p in result.ports]
        assert 8080 in host_ports

    def test_port_container_port(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        p = next(p for p in result.ports if p.host_port == 8080)
        assert p.container_port == 80

    def test_port_protocol_explicit_tcp(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        p = next(p for p in result.ports if p.host_port == 8080)
        assert p.protocol == "tcp"

    def test_port_protocol_default_tcp(self):
        """port without protocol defaults to tcp."""
        result = self._parse("port 5432:5432\n")
        assert result.ports[0].protocol == "tcp"

    def test_port_both_sides(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        p5432 = next(p for p in result.ports if p.host_port == 5432)
        assert p5432.container_port == 5432

    # --- Env ---

    def test_env_key_value(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        assert result.env.get("API_KEY") == "secret123"

    def test_env_second_entry(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        assert result.env.get("DEBUG") == "1"

    def test_env_count(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        assert len(result.env) == 2

    def test_env_value_with_equals_sign(self):
        """Values that themselves contain '=' must be preserved."""
        result = self._parse("env TOKEN=abc=def\n")
        assert result.env["TOKEN"] == "abc=def"

    # --- Mise ---

    def test_mise_is_true_when_directive_present(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        assert result.mise is True

    def test_mise_is_false_when_absent(self):
        result = self._parse("# no mise directive\n")
        assert result.mise is False

    # --- Memory ---

    def test_memory_value(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        assert result.memory_limit is not None
        assert result.memory_limit.value == 512

    def test_memory_unit(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        assert result.memory_limit.unit == "m"

    def test_memory_absent_is_none(self):
        result = self._parse("# no memory\n")
        assert result.memory_limit is None

    def test_memory_various_units(self):
        for unit in ("b", "k", "m", "g"):
            result = self._parse(f"memory 256{unit}\n")
            assert result.memory_limit.unit == unit
            assert result.memory_limit.value == 256

    # --- Runtime ---

    def test_runtime_docker(self):
        text = VALID_CONFIG.read_text()
        result = self._parse(text)
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert result.runtime == RuntimeKind.DOCKER

    def test_runtime_podman(self):
        result = self._parse("runtime podman\n")
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert result.runtime == RuntimeKind.PODMAN

    def test_runtime_auto(self):
        result = self._parse("runtime auto\n")
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert result.runtime == RuntimeKind.AUTO

    def test_runtime_default_is_auto(self):
        result = self._parse("# no runtime directive\n")
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert result.runtime == RuntimeKind.AUTO

    def test_runtime_case_insensitive(self):
        result = self._parse("runtime DOCKER\n")
        from agent_sandbox.domain.value_objects import RuntimeKind
        assert result.runtime == RuntimeKind.DOCKER

    # --- Comments and blank lines ---

    def test_comments_are_ignored(self):
        result = self._parse("# this is a comment\n")
        assert len(result.volumes) == 0

    def test_blank_lines_are_ignored(self):
        result = self._parse("\n\n\n")
        assert len(result.volumes) == 0

    def test_inline_comment_not_supported_but_no_crash(self):
        """Trailing comments are treated as part of the directive value."""
        # If inline comments cause issues, they raise SandboxError; either way
        # we do not crash with an unhandled Python exception.
        try:
            self._parse("mise # enable mise\n")
        except Exception as exc:
            from agent_sandbox.exceptions import SandboxError
            assert isinstance(exc, SandboxError)


# ---------------------------------------------------------------------------
# 4. Error handling
# ---------------------------------------------------------------------------

class TestParseConfigErrors:
    """Malformed or unsupported directives raise SandboxError(CONFIG_MALFORMED)."""

    def _parse(self, text: str):
        from agent_sandbox.application.use_cases.parse_config import ParseConfigUseCase
        use_case = ParseConfigUseCase()
        return use_case.execute(text)

    def test_malformed_fixture_raises_sandbox_error(self):
        from agent_sandbox.exceptions import SandboxError
        text = MALFORMED_CONFIG.read_text()
        with pytest.raises(SandboxError) as exc_info:
            self._parse(text)
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_malformed_error_has_human_readable_message(self):
        from agent_sandbox.exceptions import SandboxError
        text = MALFORMED_CONFIG.read_text()
        with pytest.raises(SandboxError) as exc_info:
            self._parse(text)
        # Must have a non-empty, actionable message
        msg = str(exc_info.value)
        assert len(msg) > 10

    def test_malformed_volume_raises_config_malformed(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("volume notapath\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_malformed_port_not_integer_raises(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("port abc:80\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_port_out_of_range_raises(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("port 99999:80\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_malformed_port_missing_colon_raises(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("port 8080\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_unknown_directive_raises_sandbox_error(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("unknowndirective foobar\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_malformed_env_no_equals_raises(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("env NOEQUALS\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_malformed_memory_bad_unit_raises(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("memory 512tb\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_malformed_memory_no_value_raises(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("memory\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_malformed_runtime_unknown_raises(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("runtime kubernetes\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_volume_path_traversal_rejected(self):
        """Volume host_path with ../ traversal must be rejected."""
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("volume /tmp/../etc:/workspace:rw\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_volume_relative_host_path_rejected(self):
        """Relative paths in volume host_path must be rejected."""
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("volume relative/path:/workspace:rw\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"


# ---------------------------------------------------------------------------
# 5. SandboxConfig.from_file facade
# ---------------------------------------------------------------------------

class TestSandboxConfigFromFile:
    """SandboxConfig.from_file wires FileConfigSource + ParseConfigUseCase."""

    def test_from_file_returns_sandbox_config(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert isinstance(cfg, SandboxConfig)

    def test_from_file_accepts_path_object(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert cfg is not None

    def test_from_file_accepts_string_path(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(str(VALID_CONFIG))
        assert cfg is not None

    def test_from_file_records_config_path(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert cfg.config_path == Path(VALID_CONFIG)

    def test_from_file_records_source_filename_agent_sandbox(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        # The source_filename is the basename of the file passed to from_file.
        # Our fixture is named "valid.agent-sandbox".
        assert cfg.source_filename == VALID_CONFIG.name

    def test_from_file_parses_volumes(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert len(cfg.volumes) == 2

    def test_from_file_parses_ports(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert len(cfg.ports) == 2

    def test_from_file_parses_env(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert cfg.env["API_KEY"] == "secret123"

    def test_from_file_parses_mise(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert cfg.mise is True

    def test_from_file_parses_memory(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert cfg.memory_limit is not None
        assert cfg.memory_limit.value == 512

    def test_from_file_parses_runtime(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.domain.value_objects import RuntimeKind
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert cfg.runtime == RuntimeKind.DOCKER

    def test_from_file_missing_file_raises_sandbox_error(self):
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError):
            SandboxConfig.from_file(Path("/nonexistent/.agent-sandbox"))

    def test_from_file_missing_file_not_raw_file_not_found(self):
        """FileNotFoundError must not bubble up — only SandboxError."""
        from agent_sandbox.domain.entities import SandboxConfig
        from agent_sandbox.exceptions import SandboxError
        try:
            SandboxConfig.from_file(Path("/nonexistent/.agent-sandbox"))
        except SandboxError:
            pass  # correct
        except FileNotFoundError:
            pytest.fail("FileNotFoundError must be wrapped in SandboxError")


# ---------------------------------------------------------------------------
# 6. claude-config directive
# ---------------------------------------------------------------------------

class TestClaudeConfigDirective:
    """ParseConfigUseCase handles the claude-config directive correctly."""

    def _parse(self, text: str):
        from agent_sandbox.application.use_cases.parse_config import ParseConfigUseCase
        return ParseConfigUseCase().execute(text)

    def test_absent_directive_yields_none(self):
        result = self._parse("# no claude-config\n")
        assert result.claude_config_dir is None

    def test_absolute_path_parsed(self):
        result = self._parse("claude-config /home/alice/.claude-acme\n")
        assert result.claude_config_dir == Path("/home/alice/.claude-acme")

    def test_tilde_expanded_to_home(self):
        result = self._parse("claude-config ~/.claude-acme\n")
        expected = Path("~/.claude-acme").expanduser()
        assert result.claude_config_dir == expected

    def test_result_is_path_object(self):
        result = self._parse("claude-config /tmp/some-config\n")
        assert isinstance(result.claude_config_dir, Path)

    def test_empty_arg_raises_config_malformed(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("claude-config\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_relative_path_raises_config_malformed(self):
        from agent_sandbox.exceptions import SandboxError
        with pytest.raises(SandboxError) as exc_info:
            self._parse("claude-config relative/path\n")
        assert exc_info.value.code == "CONFIG_MALFORMED"

    def test_coexists_with_other_directives(self):
        text = (
            "volume /src:/workspace:rw\n"
            "claude-config /home/alice/.claude-acme\n"
            "port 8080:80\n"
        )
        result = self._parse(text)
        assert result.claude_config_dir == Path("/home/alice/.claude-acme")
        assert len(result.volumes) == 1
        assert len(result.ports) == 1


class TestSandboxConfigFromFileAlias:
    """SandboxConfig.from_file accepts .claude-sandbox as a backward-compat alias."""

    def _make_claude_sandbox(self, tmp_path: Path) -> Path:
        """Create a minimal .claude-sandbox file in tmp_path."""
        p = tmp_path / ".claude-sandbox"
        p.write_text("# backward-compat alias\nvolume /src:/workspace:rw\n")
        return p

    def test_from_file_accepts_claude_sandbox_filename(self, tmp_path):
        from agent_sandbox.domain.entities import SandboxConfig
        path = self._make_claude_sandbox(tmp_path)
        cfg = SandboxConfig.from_file(path)
        assert cfg is not None

    def test_from_file_claude_sandbox_records_source_filename(self, tmp_path):
        from agent_sandbox.domain.entities import SandboxConfig
        path = self._make_claude_sandbox(tmp_path)
        cfg = SandboxConfig.from_file(path)
        assert cfg.source_filename == ".claude-sandbox"

    def test_from_file_claude_sandbox_records_config_path(self, tmp_path):
        from agent_sandbox.domain.entities import SandboxConfig
        path = self._make_claude_sandbox(tmp_path)
        cfg = SandboxConfig.from_file(path)
        assert cfg.config_path == path

    def test_from_file_claude_sandbox_parses_volume(self, tmp_path):
        from agent_sandbox.domain.entities import SandboxConfig
        path = self._make_claude_sandbox(tmp_path)
        cfg = SandboxConfig.from_file(path)
        assert len(cfg.volumes) == 1
        assert cfg.volumes[0].host_path == "/src"


# ---------------------------------------------------------------------------
# 6. Provenance: source_filename and config_path recorded
# ---------------------------------------------------------------------------

class TestProvenance:
    """Provenance fields must be set by from_file and preserved correctly."""

    def test_config_path_is_path_object(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert isinstance(cfg.config_path, Path)

    def test_source_filename_is_string(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert isinstance(cfg.source_filename, str)

    def test_source_filename_is_basename(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert cfg.source_filename == VALID_CONFIG.name

    def test_config_path_matches_input(self):
        from agent_sandbox.domain.entities import SandboxConfig
        cfg = SandboxConfig.from_file(VALID_CONFIG)
        assert cfg.config_path == VALID_CONFIG


# ---------------------------------------------------------------------------
# 7. Import purity checks
# ---------------------------------------------------------------------------

class TestParseConfigImportPurity:
    """parse_config.py must import only domain and stdlib (application layer)."""

    def _get_ast(self) -> ast.Module:
        path = _use_case_path("parse_config.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_forbidden_infra_imports(self):
        """Application use case must not import infrastructure modules."""
        forbidden = {"subprocess", "click", "fastapi", "sqlalchemy", "flask",
                     "django", "docker", "podman"}
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, (
                        f"parse_config.py must not import '{top}' (application layer)"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"parse_config.py must not import from '{top}'"
                    )


class TestFileConfigSourceImportPurity:
    """file_config_source.py may import stdlib and domain/application (infra layer)."""

    def _get_ast(self) -> ast.Module:
        path = _infra_path("file_config_source.py")
        with open(path) as f:
            return ast.parse(f.read())

    def test_no_framework_imports(self):
        """Infrastructure module must not import heavy frameworks."""
        forbidden = {"click", "fastapi", "sqlalchemy", "flask", "django",
                     "docker", "podman"}
        tree = self._get_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, (
                        f"file_config_source.py must not import '{top}'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden, (
                        f"file_config_source.py must not import from '{top}'"
                    )
