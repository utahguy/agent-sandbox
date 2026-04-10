#!/usr/bin/env bash
# Tests for the agent-sandbox script.
#
# Stubs out podman, systemctl, and (where needed) uname so nothing actually
# runs on the container runtime. Captures the final `podman run` argv into
# a log file and asserts on it.
#
# Run: ./tests/run-tests.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
AGENT_SANDBOX="${REPO_DIR}/agent-sandbox"

PASS=0
FAIL=0
FAILED_NAMES=()

# --- Per-test sandbox --------------------------------------------------------

# Creates a fresh temp HOME, project dir, and fake-bin PATH prefix.
# Sets: TEST_TMP, TEST_HOME, TEST_PROJECT, TEST_BIN, PODMAN_LOG
setup_test() {
    TEST_TMP="$(mktemp -d)"
    TEST_HOME="${TEST_TMP}/home"
    TEST_PROJECT="${TEST_TMP}/project"
    TEST_BIN="${TEST_TMP}/bin"
    PODMAN_LOG="${TEST_TMP}/podman.log"

    mkdir -p "$TEST_HOME" "$TEST_PROJECT" "$TEST_BIN"

    # Fake podman: answers the handful of subcommands the script invokes,
    # and for `run` writes argv to PODMAN_LOG (one arg per line) and exits.
    cat > "${TEST_BIN}/podman" <<EOF
#!/usr/bin/env bash
case "\$1" in
    image)
        case "\$2" in
            exists)  exit 0 ;;                         # pretend image is built
            inspect) echo "2026-01-01T00:00:00Z" ;;    # Created timestamp
        esac
        exit 0
        ;;
    build)    exit 0 ;;
    compose)  exit 0 ;;
    inspect)  exit 0 ;;
    run)
        shift
        printf '%s\n' "\$@" > "${PODMAN_LOG}"
        exit 0
        ;;
esac
exit 0
EOF
    chmod +x "${TEST_BIN}/podman"

    # Fake systemctl (compose path asks about podman.socket)
    cat > "${TEST_BIN}/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "${TEST_BIN}/systemctl"

    export HOME="$TEST_HOME"
    export PATH="${TEST_BIN}:${PATH}"
}

teardown_test() {
    rm -rf "$TEST_TMP"
}

# --- Assertions --------------------------------------------------------------

# Assert PODMAN_LOG contains a line matching needle exactly.
assert_arg() {
    local needle="$1"
    if ! grep -qxF -- "$needle" "$PODMAN_LOG" 2>/dev/null; then
        echo "    expected arg: $needle"
        echo "    actual argv:"
        sed 's/^/      /' "$PODMAN_LOG" 2>/dev/null || echo "      (no log)"
        return 1
    fi
}

# Assert PODMAN_LOG does NOT contain a line matching needle.
refute_arg() {
    local needle="$1"
    if grep -qxF -- "$needle" "$PODMAN_LOG" 2>/dev/null; then
        echo "    unexpected arg present: $needle"
        return 1
    fi
}

# Assert a -v VOLUME arg is present (checks the value line, not the -v).
assert_volume() { assert_arg "$1"; }

# --- Test runner -------------------------------------------------------------

run_test() {
    local name="$1"
    shift
    setup_test
    local output
    if output="$("$@" 2>&1)"; then
        PASS=$((PASS + 1))
        printf '  \033[32m✓\033[0m %s\n' "$name"
    else
        FAIL=$((FAIL + 1))
        FAILED_NAMES+=("$name")
        printf '  \033[31m✗\033[0m %s\n' "$name"
        if [ -n "$output" ]; then
            printf '%s\n' "$output" | sed 's/^/      /'
        fi
    fi
    teardown_test
}

# --- Tests -------------------------------------------------------------------

test_basic_project_mount() {
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    [ -f "$PODMAN_LOG" ] || { echo "    podman run was not invoked"; return 1; }
    assert_volume "${TEST_PROJECT}:/workspace:Z" || return 1
}

test_uv_cache_mounted_linux() {
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${TEST_HOME}/.cache/uv:/home/claude/.cache/uv:Z" || return 1
    [ -d "${TEST_HOME}/.cache/uv" ] || { echo "    uv cache host dir was not created"; return 1; }
}

test_uv_cache_mounted_macos() {
    # Stub uname -s to report Darwin for this test only
    cat > "${TEST_BIN}/uname" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "-s" ]; then echo Darwin; exit 0; fi
exec /usr/bin/uname "$@"
EOF
    chmod +x "${TEST_BIN}/uname"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${TEST_HOME}/Library/Caches/uv:/home/claude/.cache/uv:Z" || return 1
    [ -d "${TEST_HOME}/Library/Caches/uv" ] || {
        echo "    macOS uv cache host dir was not created"; return 1
    }
}

test_mise_cache_mounted() {
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${TEST_HOME}/.local/share/agent-sandbox-mise:/home/claude/.local/share/mise:Z" || return 1
}

test_default_agent_is_claude() {
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "SANDBOX_AGENT=claude" || return 1
}

test_agent_flag_codex() {
    "$AGENT_SANDBOX" --agent codex "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "SANDBOX_AGENT=codex" || return 1
    # Codex mounts ~/.codex read-write
    assert_volume "${TEST_HOME}/.codex:/home/claude/.codex:Z" || return 1
}

test_agent_flag_aider() {
    "$AGENT_SANDBOX" --agent aider "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "SANDBOX_AGENT=aider" || return 1
}

test_unknown_agent_fails() {
    if "$AGENT_SANDBOX" --agent bogus "$TEST_PROJECT" >/dev/null 2>&1; then
        echo "    expected nonzero exit for unknown agent"
        return 1
    fi
}

test_volume_directive() {
    mkdir -p "${TEST_TMP}/extra"
    echo "volume: ${TEST_TMP}/extra:/opt/extra:ro" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${TEST_TMP}/extra:/opt/extra:ro" || return 1
}

test_volume_tilde_expansion() {
    mkdir -p "${TEST_HOME}/mydata"
    echo "volume: ~/mydata:/opt/mydata" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${TEST_HOME}/mydata:/opt/mydata" || return 1
}

test_port_directive() {
    echo "port: 3000" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "3000" || return 1
}

test_port_mapping_directive() {
    echo "port: 8080:80" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "8080:80" || return 1
}

test_config_comments_and_blanks_ignored() {
    cat > "${TEST_PROJECT}/.agent-sandbox" <<EOF
# leading comment

port: 9090   # trailing comment
# another comment
volume: /tmp/foo:/tmp/bar
EOF
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "9090" || return 1
    assert_volume "/tmp/foo:/tmp/bar" || return 1
}

test_claude_sandbox_fallback() {
    # When .agent-sandbox is absent, .claude-sandbox should be read
    echo "port: 7777" > "${TEST_PROJECT}/.claude-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "7777" || return 1
}

test_agent_sandbox_wins_over_claude_sandbox() {
    echo "port: 1111" > "${TEST_PROJECT}/.agent-sandbox"
    echo "port: 2222" > "${TEST_PROJECT}/.claude-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "1111" || return 1
    refute_arg "2222" || return 1
}

test_agent_env_file() {
    mkdir -p "${TEST_HOME}/.config/agent-sandbox"
    echo "OPENAI_API_KEY=sk-test" > "${TEST_HOME}/.config/agent-sandbox/claude.env"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "OPENAI_API_KEY=sk-test" || return 1
}

test_agent_env_file_comments_ignored() {
    mkdir -p "${TEST_HOME}/.config/agent-sandbox"
    cat > "${TEST_HOME}/.config/agent-sandbox/claude.env" <<EOF
# this is a comment
FOO=bar

BAZ=qux   # trailing
EOF
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "FOO=bar" || return 1
    assert_arg "BAZ=qux" || return 1
}

test_agent_args_after_dashdash() {
    "$AGENT_SANDBOX" "$TEST_PROJECT" -- --resume my-session >/dev/null || return 1
    assert_arg "--resume" || return 1
    assert_arg "my-session" || return 1
}

test_claude_auth_mounted_if_present() {
    touch "${TEST_HOME}/.claude.json"
    mkdir -p "${TEST_HOME}/.claude"
    touch "${TEST_HOME}/.claude/.credentials.json"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${TEST_HOME}/.claude.json:/home/claude/.claude.json:Z" || return 1
    assert_volume "${TEST_HOME}/.claude/.credentials.json:/home/claude/.claude/.credentials.json:Z" || return 1
}

test_claude_auth_skipped_if_absent() {
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    refute_arg "${TEST_HOME}/.claude.json:/home/claude/.claude.json:Z" || return 1
}

test_git_config_mounted_readonly() {
    touch "${TEST_HOME}/.gitconfig"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${TEST_HOME}/.gitconfig:/home/claude/.gitconfig:ro,Z" || return 1
}

test_claude_sessions_dir_created_in_project() {
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    [ -d "${TEST_PROJECT}/.claude-sessions/projects" ] || {
        echo "    .claude-sessions/projects was not created"
        return 1
    }
    [ -d "${TEST_PROJECT}/.claude-sessions/sessions" ] || {
        echo "    .claude-sessions/sessions was not created"
        return 1
    }
}

test_userns_keep_id_flag() {
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "--userns=keep-id" || return 1
}

# --- Run all -----------------------------------------------------------------

echo "Running agent-sandbox tests..."
echo

run_test "mounts project dir at /workspace"            test_basic_project_mount
run_test "uv cache mounted from ~/.cache/uv on Linux"  test_uv_cache_mounted_linux
run_test "uv cache mounted from ~/Library/Caches/uv on macOS" test_uv_cache_mounted_macos
run_test "mise cache mounted"                          test_mise_cache_mounted
run_test "default agent is claude"                     test_default_agent_is_claude
run_test "--agent codex"                               test_agent_flag_codex
run_test "--agent aider"                               test_agent_flag_aider
run_test "unknown --agent exits non-zero"              test_unknown_agent_fails
run_test "volume: directive adds mount"                test_volume_directive
run_test "volume: expands leading ~"                   test_volume_tilde_expansion
run_test "port: directive adds single port"            test_port_directive
run_test "port: directive adds host:container mapping" test_port_mapping_directive
run_test "config comments and blank lines ignored"     test_config_comments_and_blanks_ignored
run_test ".claude-sandbox used when .agent-sandbox absent" test_claude_sandbox_fallback
run_test ".agent-sandbox preferred over .claude-sandbox"   test_agent_sandbox_wins_over_claude_sandbox
run_test "agent env file adds -e vars"                 test_agent_env_file
run_test "agent env file comments ignored"             test_agent_env_file_comments_ignored
run_test "args after -- passed to agent"               test_agent_args_after_dashdash
run_test "claude auth files mounted when present"     test_claude_auth_mounted_if_present
run_test "claude auth not mounted when absent"        test_claude_auth_skipped_if_absent
run_test "git config mounted read-only"                test_git_config_mounted_readonly
run_test ".claude-sessions dir created in project"    test_claude_sessions_dir_created_in_project
run_test "--userns=keep-id is passed to podman run"   test_userns_keep_id_flag

echo
if [ "$FAIL" -eq 0 ]; then
    printf '\033[32m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
    exit 0
else
    printf '\033[31m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
    echo "Failed tests:"
    for name in "${FAILED_NAMES[@]}"; do
        echo "  - $name"
    done
    exit 1
fi
