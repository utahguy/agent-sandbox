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
    COMPOSE_LOG="${TEST_TMP}/compose.log"

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
    compose)
        shift
        printf '%s\n' "\$@" >> "${COMPOSE_LOG}"
        exit 0
        ;;
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
    # Ensure each test starts without a real ssh-agent leaking in
    unset SSH_AUTH_SOCK
}

teardown_test() {
    rm -rf "$TEST_TMP"
}

# --- Assertions --------------------------------------------------------------

# Assert `log_file` contains a line matching `needle` exactly.
assert_log_line() {
    local log_file="$1" needle="$2"
    if ! grep -qxF -- "$needle" "$log_file" 2>/dev/null; then
        echo "    expected arg: $needle"
        echo "    actual argv (${log_file}):"
        sed 's/^/      /' "$log_file" 2>/dev/null || echo "      (no log)"
        return 1
    fi
}

# Assert `log_file` does NOT contain a line matching `needle`.
refute_log_line() {
    local log_file="$1" needle="$2"
    if grep -qxF -- "$needle" "$log_file" 2>/dev/null; then
        echo "    unexpected arg present in ${log_file}: $needle"
        return 1
    fi
}

# Assert PODMAN_LOG contains a line matching needle exactly.
assert_arg() { assert_log_line "$PODMAN_LOG" "$1"; }

# Assert PODMAN_LOG does NOT contain a line matching needle.
refute_arg() { refute_log_line "$PODMAN_LOG" "$1"; }

# Assert a -v VOLUME arg is present (checks the value line, not the -v).
assert_volume() { assert_arg "$1"; }

# Assert COMPOSE_LOG contains a line matching needle exactly.
assert_compose_arg() { assert_log_line "$COMPOSE_LOG" "$1"; }

# Assert COMPOSE_LOG does NOT contain a line matching needle.
refute_compose_arg() { refute_log_line "$COMPOSE_LOG" "$1"; }

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

test_ssh_agent_forwarded_when_available() {
    # Start a real ssh-agent bound to a known socket path
    local sock="${TEST_TMP}/agent.sock"
    local agent_env
    agent_env="$(ssh-agent -a "$sock" 2>/dev/null)" || {
        echo "    could not start ssh-agent"
        return 1
    }
    local agent_pid
    agent_pid="$(echo "$agent_env" | sed -n 's/.*SSH_AGENT_PID=\([0-9]*\).*/\1/p')"
    [ -S "$sock" ] || { echo "    ssh-agent did not create socket"; kill "$agent_pid" 2>/dev/null; return 1; }

    export SSH_AUTH_SOCK="$sock"
    local rc=0
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || rc=1
    [ "$rc" -eq 0 ] && { assert_volume "${sock}:/run/host-ssh-agent.sock" || rc=1; }
    [ "$rc" -eq 0 ] && { assert_arg "SSH_AUTH_SOCK=/run/host-ssh-agent.sock" || rc=1; }

    kill "$agent_pid" 2>/dev/null
    return "$rc"
}

test_ssh_agent_skipped_when_unset() {
    unset SSH_AUTH_SOCK
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    refute_arg "SSH_AUTH_SOCK=/run/host-ssh-agent.sock" || return 1
    # The mount target should also not appear
    if grep -F ":/run/host-ssh-agent.sock" "$PODMAN_LOG" >/dev/null 2>&1; then
        echo "    ssh agent socket mounted unexpectedly"
        return 1
    fi
}

test_ssh_agent_skipped_when_socket_missing() {
    # SSH_AUTH_SOCK points to a nonexistent path
    export SSH_AUTH_SOCK="${TEST_TMP}/nonexistent.sock"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    refute_arg "SSH_AUTH_SOCK=/run/host-ssh-agent.sock" || return 1
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

# --- Per-project container tests ---------------------------------------------

# Helper: replace fake podman with one that also logs build calls.
# Sets BUILD_LOG to the path where build argv is captured.
setup_build_logging_podman() {
    BUILD_LOG="${TEST_TMP}/build.log"
    cat > "${TEST_BIN}/podman" <<EOF
#!/usr/bin/env bash
case "\$1" in
    image)
        case "\$2" in
            exists)
                # Check if we should pretend the project image is missing
                if [ -f "${TEST_TMP}/image_missing" ] && echo "\$3" | grep -qv "^agent-sandbox-"; then
                    exit 0
                fi
                if [ -f "${TEST_TMP}/image_missing" ]; then
                    exit 1
                fi
                exit 0
                ;;
            inspect) echo "2026-01-01T00:00:00Z" ;;
        esac
        exit 0
        ;;
    build)
        printf '%s\n' "\$@" > "${BUILD_LOG}"
        exit 0
        ;;
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
}

test_no_project_containerfile_unchanged() {
    # No Containerfile or Dockerfile in project — base image used, no project build
    setup_build_logging_podman
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    # The image name used for `run` should be the base, not a derived one
    local project_name
    project_name="$(basename "$TEST_PROJECT")"
    refute_arg "agent-sandbox-$(id -u)-${project_name}" || return 1
}

test_project_containerfile_builds_derived_image() {
    echo "RUN echo test" > "${TEST_PROJECT}/Containerfile"
    touch -t 202701010000 "${TEST_TMP}/image_missing"
    setup_build_logging_podman
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    [ -f "$BUILD_LOG" ] || { echo "    podman build was not invoked"; return 1; }
    # Build should reference BASE_IMAGE
    grep -qF "BASE_IMAGE=" "$BUILD_LOG" || { echo "    BASE_IMAGE arg missing from build"; return 1; }
    # Build should use the project Containerfile
    grep -qF "${TEST_PROJECT}/Containerfile" "$BUILD_LOG" || {
        echo "    build did not use project Containerfile"; return 1
    }
}

test_project_dockerfile_used_when_no_containerfile() {
    echo "RUN echo test" > "${TEST_PROJECT}/Dockerfile"
    touch -t 202701010000 "${TEST_TMP}/image_missing"
    setup_build_logging_podman
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    [ -f "$BUILD_LOG" ] || { echo "    podman build was not invoked"; return 1; }
    grep -qF "${TEST_PROJECT}/Dockerfile" "$BUILD_LOG" || {
        echo "    build did not use project Dockerfile"; return 1
    }
}

test_project_containerfile_preferred_over_dockerfile() {
    echo "RUN echo containerfile" > "${TEST_PROJECT}/Containerfile"
    echo "RUN echo dockerfile" > "${TEST_PROJECT}/Dockerfile"
    touch -t 202701010000 "${TEST_TMP}/image_missing"
    setup_build_logging_podman
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    [ -f "$BUILD_LOG" ] || { echo "    podman build was not invoked"; return 1; }
    grep -qF "${TEST_PROJECT}/Containerfile" "$BUILD_LOG" || {
        echo "    build did not prefer Containerfile over Dockerfile"; return 1
    }
}

test_derived_image_used_for_run() {
    echo "RUN echo test" > "${TEST_PROJECT}/Containerfile"
    touch -t 202701010000 "${TEST_TMP}/image_missing"
    setup_build_logging_podman
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    # The podman run log should contain the derived image name
    local project_name
    project_name="$(basename "$TEST_PROJECT")"
    assert_arg "agent-sandbox-$(id -u)-${project_name}" || return 1
}

test_env_directive() {
    echo "env: SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T/B/xxx" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T/B/xxx" || return 1
}

test_env_directive_multiple() {
    cat > "${TEST_PROJECT}/.agent-sandbox" <<EOF
env: FOO=bar
env: BAZ=qux
EOF
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "FOO=bar" || return 1
    assert_arg "BAZ=qux" || return 1
}

test_env_directive_with_other_directives() {
    cat > "${TEST_PROJECT}/.agent-sandbox" <<EOF
port: 3000
env: MY_VAR=hello
volume: /tmp/foo:/tmp/bar
EOF
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "3000" || return 1
    assert_arg "MY_VAR=hello" || return 1
    assert_volume "/tmp/foo:/tmp/bar" || return 1
}

test_no_containerfile_falls_back_to_base() {
    # Simulates: project Containerfile was deleted
    # No Containerfile or Dockerfile present
    setup_build_logging_podman
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    # Build log may exist from the base image build, but it should NOT
    # reference a project-derived image name
    local project_name
    project_name="$(basename "$TEST_PROJECT")"
    if [ -f "$BUILD_LOG" ] && grep -qF -- "-${project_name}" "$BUILD_LOG"; then
        echo "    unexpected project-specific build triggered"
        return 1
    fi
}

# --- claude-config: directive tests -----------------------------------------

test_claude_config_credentials_mounted_from_named_dir() {
    local alt_config="${TEST_HOME}/.claude-acme"
    mkdir -p "${alt_config}"
    touch "${alt_config}/.credentials.json"
    echo "claude-config: ${alt_config}" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${alt_config}/.credentials.json:/home/claude/.claude/.credentials.json:Z" || return 1
}

test_claude_config_tilde_expanded() {
    mkdir -p "${TEST_HOME}/.claude-acme"
    touch "${TEST_HOME}/.claude-acme/.credentials.json"
    echo "claude-config: ~/.claude-acme" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${TEST_HOME}/.claude-acme/.credentials.json:/home/claude/.claude/.credentials.json:Z" || return 1
}

test_claude_config_overrides_default_credentials() {
    # Default ~/.claude/.credentials.json must NOT be mounted when claude-config: is set
    mkdir -p "${TEST_HOME}/.claude"
    touch "${TEST_HOME}/.claude/.credentials.json"
    local alt_config="${TEST_HOME}/.claude-acme"
    mkdir -p "${alt_config}"
    touch "${alt_config}/.credentials.json"
    echo "claude-config: ${alt_config}" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_volume "${alt_config}/.credentials.json:/home/claude/.claude/.credentials.json:Z" || return 1
    refute_arg "${TEST_HOME}/.claude/.credentials.json:/home/claude/.claude/.credentials.json:Z" || return 1
}

test_claude_config_missing_dir_does_not_error() {
    echo "claude-config: ${TEST_HOME}/.claude-nonexistent" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null 2>&1 || return 1
}

test_claude_config_settings_mounted_readonly() {
    local alt_config="${TEST_HOME}/.claude-acme"
    mkdir -p "${alt_config}"
    echo '{}' > "${alt_config}/settings.json"
    echo "claude-config: ${alt_config}" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    # settings.json is copied to a temp dir and mounted at /tmp/claude-settings-src read-only
    if ! grep -F ":/tmp/claude-settings-src:ro,Z" "$PODMAN_LOG" >/dev/null 2>&1; then
        echo "    settings.json not mounted r/o at /tmp/claude-settings-src"
        return 1
    fi
}

test_claude_config_coexists_with_other_directives() {
    local alt_config="${TEST_HOME}/.claude-acme"
    mkdir -p "${alt_config}"
    touch "${alt_config}/.credentials.json"
    cat > "${TEST_PROJECT}/.agent-sandbox" <<EOF
port: 3000
claude-config: ${alt_config}
env: MY_VAR=hello
EOF
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_arg "3000" || return 1
    assert_arg "MY_VAR=hello" || return 1
    assert_volume "${alt_config}/.credentials.json:/home/claude/.claude/.credentials.json:Z" || return 1
}

test_compose_directive_relative_path() {
    mkdir -p "${TEST_PROJECT}/server"
    touch "${TEST_PROJECT}/server/compose.yml"
    echo "compose: server/compose.yml" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_compose_arg "${TEST_PROJECT}/server/compose.yml" || return 1
}

test_compose_directive_tilde_expansion() {
    mkdir -p "${TEST_HOME}/shared"
    touch "${TEST_HOME}/shared/compose.yml"
    echo "compose: ~/shared/compose.yml" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_compose_arg "${TEST_HOME}/shared/compose.yml" || return 1
}

test_compose_directive_used_even_with_root_file_present() {
    # A compose.yml at the project root must NOT be auto-detected...
    touch "${TEST_PROJECT}/compose.yml"
    # ...only the explicit directive's target is started.
    mkdir -p "${TEST_PROJECT}/alt"
    touch "${TEST_PROJECT}/alt/compose.yml"
    echo "compose: alt/compose.yml" > "${TEST_PROJECT}/.agent-sandbox"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    assert_compose_arg "${TEST_PROJECT}/alt/compose.yml" || return 1
    refute_compose_arg "${TEST_PROJECT}/compose.yml" || return 1
}

test_compose_no_directive_does_not_autostart() {
    # A compose.yml at the project root with no "compose:" directive in
    # .agent-sandbox must not start any services at all.
    touch "${TEST_PROJECT}/compose.yml"
    "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null || return 1
    if [ -f "$COMPOSE_LOG" ]; then
        echo "    compose was invoked even though no compose: directive was set"
        return 1
    fi
}

test_compose_directive_missing_file_errors() {
    echo "compose: does-not-exist.yml" > "${TEST_PROJECT}/.agent-sandbox"
    if "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null 2>&1; then
        echo "    expected nonzero exit when compose: file is missing"
        return 1
    fi
}

test_compose_directive_missing_file_fails_before_build() {
    # Force both the base and any derived image to look missing, so a build
    # would normally be triggered — the missing compose: file must be caught
    # before that build ever runs.
    setup_build_logging_podman
    touch "${TEST_TMP}/image_missing"
    echo "compose: does-not-exist.yml" > "${TEST_PROJECT}/.agent-sandbox"
    if "$AGENT_SANDBOX" "$TEST_PROJECT" >/dev/null 2>&1; then
        echo "    expected nonzero exit when compose: file is missing"
        return 1
    fi
    if [ -f "$BUILD_LOG" ]; then
        echo "    image build was invoked even though the compose file is missing"
        return 1
    fi
}

test_compose_directive_duplicate_warns_and_last_wins() {
    mkdir -p "${TEST_PROJECT}/a" "${TEST_PROJECT}/b"
    touch "${TEST_PROJECT}/a/compose.yml" "${TEST_PROJECT}/b/compose.yml"
    cat > "${TEST_PROJECT}/.agent-sandbox" <<EOF
compose: a/compose.yml
compose: b/compose.yml
EOF
    local stderr_output
    stderr_output="$("$AGENT_SANDBOX" "$TEST_PROJECT" 2>&1 >/dev/null)" || return 1
    if ! echo "$stderr_output" | grep -q "multiple 'compose:' directives"; then
        echo "    expected a duplicate-directive warning, got:"
        echo "$stderr_output"
        return 1
    fi
    assert_compose_arg "${TEST_PROJECT}/b/compose.yml" || return 1
    refute_compose_arg "${TEST_PROJECT}/a/compose.yml" || return 1
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
run_test "ssh agent forwarded when SSH_AUTH_SOCK set"  test_ssh_agent_forwarded_when_available
run_test "ssh agent not forwarded when unset"          test_ssh_agent_skipped_when_unset
run_test "ssh agent not forwarded when socket missing" test_ssh_agent_skipped_when_socket_missing
run_test ".claude-sessions dir created in project"    test_claude_sessions_dir_created_in_project
run_test "--userns=keep-id is passed to podman run"   test_userns_keep_id_flag
run_test "env: directive sets env var"                        test_env_directive
run_test "env: directive multiple vars"                      test_env_directive_multiple
run_test "env: directive with port: and volume:"             test_env_directive_with_other_directives
run_test "no project Containerfile — behavior unchanged"      test_no_project_containerfile_unchanged
run_test "project Containerfile triggers derived build"       test_project_containerfile_builds_derived_image
run_test "project Dockerfile used when no Containerfile"      test_project_dockerfile_used_when_no_containerfile
run_test "project Containerfile preferred over Dockerfile"    test_project_containerfile_preferred_over_dockerfile
run_test "derived image name used for podman run"             test_derived_image_used_for_run
run_test "no Containerfile falls back to base image"          test_no_containerfile_falls_back_to_base
run_test "claude-config: credentials mounted from named dir"  test_claude_config_credentials_mounted_from_named_dir
run_test "claude-config: ~ expanded to home dir"              test_claude_config_tilde_expanded
run_test "claude-config: overrides default ~/.claude creds"   test_claude_config_overrides_default_credentials
run_test "claude-config: missing dir does not error"          test_claude_config_missing_dir_does_not_error
run_test "claude-config: settings.json mounted read-only"     test_claude_config_settings_mounted_readonly
run_test "claude-config: coexists with other directives"      test_claude_config_coexists_with_other_directives
run_test "compose: directive resolves relative path"          test_compose_directive_relative_path
run_test "compose: directive expands leading ~"                test_compose_directive_tilde_expansion
run_test "compose: directive used even with root file present" test_compose_directive_used_even_with_root_file_present
run_test "no compose: directive does not auto-start services"  test_compose_no_directive_does_not_autostart
run_test "compose: directive errors when file missing"         test_compose_directive_missing_file_errors
run_test "compose: missing file fails before image build"      test_compose_directive_missing_file_fails_before_build
run_test "compose: duplicate directive warns, last one wins"    test_compose_directive_duplicate_warns_and_last_wins

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
