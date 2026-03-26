# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

agent-sandbox runs AI coding agents (Claude Code, Codex, Aider) inside unrestricted Podman containers, with per-project toolchain management via mise.

## Repository Structure

- `agent-sandbox` — main entry script, symlinked to `~/.local/bin/agent-sandbox`
- `container/Containerfile` — Debian-based image with Claude Code, gh CLI, and mise pre-installed
- `container/entrypoint.sh` — sets up SSH, gh, mise toolchains, then dispatches to the selected agent

## Key Design Decisions

- **Single shared image** across all agents and projects; agent-specific tools (Codex, Aider) install on demand at first use
- **Mise toolchain cache** persisted on host at `~/.local/share/agent-sandbox-mise`, shared across all projects
- **Auth strategy varies by agent**: Claude uses mounted r/w token files; Codex uses mounted r/w `~/.codex/`; Aider uses API keys via env files at `~/.config/agent-sandbox/<agent>.env`
- **Git/SSH/gh config** mounted read-only from host; `known_hosts` copied writable
- **Container user** is `claude` (UID/GID mapped from host via `--userns=keep-id`)
- Per-project mounts and ports via `.agent-sandbox` file (falls back to `.claude-sandbox`)

## Conventions

- Shell scripts use `set -euo pipefail`
- Targets Podman (not Docker) with rootless operation
- Documentation in org-mode format (`.org`), never markdown (except CLAUDE.md)
