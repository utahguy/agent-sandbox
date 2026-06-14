# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

agent-sandbox runs AI coding agents (Claude Code, Codex, Aider) inside isolated OCI containers, with per-project toolchain management via mise. Available as both a Bash CLI script and a Python library.

## Repository Structure

### Bash CLI (original)
- `agent-sandbox` — main entry script, symlinked to `~/.local/bin/agent-sandbox`
- `container/Containerfile` — Debian-based image with Claude Code, gh CLI, and mise pre-installed
- `container/entrypoint.sh` — sets up SSH, gh, mise toolchains, then dispatches to the selected agent

### Python Library
- `src/agent_sandbox/` — installable Python package (`pip install -e .`)
  - `domain/` — pure entities and value objects (zero framework imports)
    - `entities.py` — SandboxConfig, ContainerHandle, ExecResult
    - `value_objects.py` — Volume, PortMapping, MemoryLimit, RuntimeKind
    - `image_spec.py` — container image specification
  - `application/` — use cases and port interfaces
    - `ports.py` — ContainerPort, ConfigSourcePort, RuntimeDetectorPort
    - `use_cases/` — parse_config, detect_runtime, ensure_image, start_sandbox, stop_sandbox, execute_command, run_agent
  - `infrastructure/` — concrete adapters
    - `container_adapter.py` — Docker/Podman container lifecycle
    - `file_config_source.py` — .agent-sandbox file parser
    - `image_builder.py` — container image builder
    - `subprocess_runtime.py` — runtime detection via subprocess
  - `facade.py` — public Sandbox API (context manager)
  - `cli/main.py` — Python CLI entry point
  - `exceptions.py` — typed exception hierarchy with error codes
- `tests/` — 579 passing tests (domain, application, infrastructure, E2E)
- `pyproject.toml` — package metadata and pytest config

## Key Design Decisions

- **Clean Architecture** — domain has zero framework imports; application defines ports; infrastructure implements them
- **Single shared image** across all agents and projects; agent-specific tools install on demand
- **Mise toolchain cache** persisted on host at `~/.local/share/agent-sandbox-mise/`, shared across all projects
- **Auth strategy varies by agent**: Claude uses mounted r/w token files; Codex uses mounted r/w `~/.codex/`; Aider uses API keys via env files
- **Git/SSH/gh config** mounted read-only from host; `known_hosts` copied writable
- **Container user** is `claude` (UID/GID mapped from host via `--userns=keep-id`)
- **Runtime auto-detection** — prefers Podman, falls back to Docker
- Per-project mounts, ports, and env via `.agent-sandbox` file (falls back to `.claude-sandbox`)

## Conventions

- Shell scripts use `set -euo pipefail`
- Python follows Clean Architecture dependency rule: domain ← application ← infrastructure
- Documentation in org-mode format (`.org`), never markdown (except CLAUDE.md)
- Tests use pytest; run with `python -m pytest tests/ -v`

## Testing

```bash
# Python library tests (579 tests)
python -m pytest tests/ -v

# Bash CLI tests (stubs podman, no runtime needed)
./tests/run-tests.sh
```
