#!/usr/bin/env bash
# Container entrypoint for agent-sandbox.
#
# Phase 1 (root): Install project-declared apt packages from
#   SANDBOX_APT_PACKAGES, then drop privileges to the project user via
#   setpriv(1) and re-exec this script as that user.
#
# Phase 2 (project user): Dispatches to the selected AI agent based on
#   $SANDBOX_AGENT env var. Handles shared setup (SSH, gh, mise) and
#   agent-specific auth.

set -euo pipefail

# ---------------------------------------------------------------------------
# Phase 1 — root-only: install packages, then drop to project user
# ---------------------------------------------------------------------------
if [ "$(id -u)" = "0" ]; then
    if [ -n "${SANDBOX_APT_PACKAGES:-}" ]; then
        echo "[agent-sandbox] Installing packages: ${SANDBOX_APT_PACKAGES}"
        apt-get update -qq
        # Word-split intentional: SANDBOX_APT_PACKAGES is a space-separated list
        # shellcheck disable=SC2086
        apt-get install -y --no-install-recommends ${SANDBOX_APT_PACKAGES}
        rm -rf /var/lib/apt/lists/*
    fi
    # Drop privileges and re-exec as the project user.
    # setpriv switches UID/GID but does not update HOME, so set it explicitly
    # so that mise and other tools find the right home directory.
    USER_HOME=$(getent passwd "${USER_UID:-1000}" | cut -d: -f6)
    export HOME="${USER_HOME:-/home/claude}"
    exec setpriv \
        --reuid="${USER_UID:-1000}" \
        --regid="${USER_GID:-1000}" \
        --init-groups \
        "$0" "$@"
fi

# ---------------------------------------------------------------------------
# Phase 2 — project user session begins here
# ---------------------------------------------------------------------------

SANDBOX_AGENT="${SANDBOX_AGENT:-claude}"
USER_HOME="/home/claude"

# Ensure .claude directory exists (used by claude agent and for settings)
mkdir -p "${USER_HOME}/.claude"

# Claude settings — copy from read-only mounts
if [ -f /tmp/claude-settings-src ]; then
    cp -f /tmp/claude-settings-src "${USER_HOME}/.claude/settings.json"
fi
if [ -f /tmp/claude-settings-local-src ]; then
    cp -f /tmp/claude-settings-local-src "${USER_HOME}/.claude/settings.local.json"
fi

# SSH setup — symlink keys from read-only mount, writable known_hosts
if [ -d "${USER_HOME}/.ssh-host" ]; then
    mkdir -p "${USER_HOME}/.ssh"
    chmod 700 "${USER_HOME}/.ssh"
    for f in "${USER_HOME}"/.ssh-host/*; do
        [ -e "$f" ] || continue
        ln -sf "$f" "${USER_HOME}/.ssh/$(basename "$f")"
    done
    # known_hosts needs to be writable (ssh appends new hosts)
    rm -f "${USER_HOME}/.ssh/known_hosts"
    cp "${USER_HOME}/.ssh-host/known_hosts" "${USER_HOME}/.ssh/known_hosts" 2>/dev/null || touch "${USER_HOME}/.ssh/known_hosts"
    chmod 600 "${USER_HOME}/.ssh/known_hosts"
fi

# GitHub CLI config — copy from read-only mount so gh can write state
if [ -d "${USER_HOME}/.config/gh-host" ]; then
    mkdir -p "${USER_HOME}/.config/gh"
    cp "${USER_HOME}/.config/gh-host"/* "${USER_HOME}/.config/gh/" 2>/dev/null || true
    chmod 600 "${USER_HOME}/.config/gh"/* 2>/dev/null || true
fi

# Trust project config before activation to avoid "not trusted" errors
if [ -f /workspace/mise.toml ] || [ -f /workspace/.mise.toml ]; then
    mise trust /workspace 2>/dev/null || true
fi

# Activate mise in this shell and for future interactive shells
eval "$(mise activate bash)"
echo 'eval "$(mise activate bash)"' >> "${USER_HOME}/.bashrc"

# Install project toolchains if a mise config or idiomatic version file exists
if [ -f /workspace/mise.toml ] || [ -f /workspace/.mise.toml ] || \
   [ -f /workspace/.tool-versions ] || [ -f /workspace/.python-version ] || \
   [ -f /workspace/.node-version ] || [ -f /workspace/.nvmrc ] || \
   [ -f /workspace/.ruby-version ] || [ -f /workspace/.go-version ]; then
    echo "Installing project toolchains via mise..."
    cd /workspace && mise install --yes
fi

# --- Agent dispatch ---
case "$SANDBOX_AGENT" in
    claude)
        exec claude --dangerously-skip-permissions "$@"
        ;;
    codex)
        if ! command -v codex &>/dev/null; then
            echo "Installing Codex CLI..."
            npm install -g @openai/codex 2>/dev/null || {
                echo "Codex requires Node.js. Add a .mise.toml with [tools] node = \"22\"" >&2
                exit 1
            }
        fi
        exec codex --full-auto "$@"
        ;;
    aider)
        if ! command -v aider &>/dev/null; then
            echo "Installing aider..."
            pip install --quiet --break-system-packages aider-chat 2>/dev/null || {
                echo "Aider requires Python. Add a .mise.toml with [tools] python = \"3.12\"" >&2
                exit 1
            }
        fi
        exec aider "$@"
        ;;
    *)
        echo "Unknown agent: ${SANDBOX_AGENT}" >&2
        echo "Supported agents: claude, codex, aider" >&2
        exit 1
        ;;
esac
