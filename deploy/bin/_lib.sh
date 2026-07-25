#!/bin/sh

# Shared portable-deployment helpers. Callers enable `set -eu` themselves so
# this file can also be sourced by the contract tests.

BIN_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEPLOY_DIR=$(CDPATH= cd -- "$BIN_DIR/.." && pwd)
APP_ROOT=$(CDPATH= cd -- "$DEPLOY_DIR/.." && pwd)

DEPLOY_MODE=${ENMOTION_DEPLOY_MODE:-mac}
ENV_FILE=${ENMOTION_ENV_FILE:-$DEPLOY_DIR/.env.server}
BASE_COMPOSE=$DEPLOY_DIR/compose.yaml
SOURCE_IDENTITY_FILE=$DEPLOY_DIR/SOURCE_IDENTITY
SOURCE_MANIFEST_FILE=$DEPLOY_DIR/SOURCE_MANIFEST.sha256
IMAGE_PROVENANCE_READY=false

case "$DEPLOY_MODE" in
    mac)
        OVERRIDE_COMPOSE=$DEPLOY_DIR/compose.mac.yaml
        ;;
    production)
        OVERRIDE_COMPOSE=$DEPLOY_DIR/compose.production.yaml
        ;;
    *)
        printf 'ERROR: unsupported deployment mode: %s (expected mac or production)\n' "$DEPLOY_MODE" >&2
        exit 2
        ;;
esac

export ENMOTION_SERVICE_ENV_FILE=$ENV_FILE

info() {
    printf '[enmotion] %s\n' "$*"
}

warn() {
    printf '[enmotion] WARNING: %s\n' "$*" >&2
}

die() {
    printf '[enmotion] ERROR: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_env_file() {
    [ -f "$ENV_FILE" ] || die "missing $ENV_FILE; run deploy/bin/bootstrap first"
}

env_value() {
    key=$1
    file=${2:-$ENV_FILE}
    awk -v wanted="$key" '
        /^[[:space:]]*#/ { next }
        index($0, "=") == 0 { next }
        {
            candidate = substr($0, 1, index($0, "=") - 1)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", candidate)
            if (candidate == wanted) {
                value = substr($0, index($0, "=") + 1)
                sub(/^[[:space:]]+/, "", value)
                sub(/[[:space:]]+$/, "", value)
                print value
                exit
            }
        }
    ' "$file"
}

set_env_value() {
    key=$1
    value=$2
    file=${3:-$ENV_FILE}
    case "$value" in
        *'\n'*|*'\r'*) die "refusing to put a newline in $key" ;;
    esac
    temp_file=$(mktemp "${file}.tmp.XXXXXX") || die "could not create temporary environment file"
    if ! awk -v wanted="$key" -v replacement="$value" '
        BEGIN { found = 0 }
        {
            line = $0
            if (line !~ /^[[:space:]]*#/ && index(line, "=") > 0) {
                candidate = substr(line, 1, index(line, "=") - 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", candidate)
                if (candidate == wanted) {
                    print wanted "=" replacement
                    found = 1
                    next
                }
            }
            print line
        }
        END { if (!found) print wanted "=" replacement }
    ' "$file" >"$temp_file"; then
        rm -f -- "$temp_file"
        die "could not update $file"
    fi
    chmod 0600 "$temp_file"
    mv -f -- "$temp_file" "$file"
}

random_hex() {
    bytes=${1:-32}
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
    else
        od -An -N "$bytes" -tx1 /dev/urandom | tr -d ' \n'
    fi
}

compose() {
    prepare_image_build_provenance
    docker compose \
        --env-file "$ENV_FILE" \
        -f "$BASE_COMPOSE" \
        -f "$OVERRIDE_COMPOSE" \
        "$@"
}

check_compose_version() {
    raw_version=$(docker compose version --short 2>/dev/null | sed 's/^v//') || return 1
    minimum=2.24.0
    if ! awk -v actual="$raw_version" -v minimum="$minimum" '
        function numeric(version, parts) {
            split(version, parts, ".")
            return (parts[1] + 0) * 1000000 + (parts[2] + 0) * 1000 + (parts[3] + 0)
        }
        BEGIN { exit(numeric(actual) < numeric(minimum)) }
    '; then
        die "Docker Compose $minimum or newer is required (found $raw_version)"
    fi
}

validate_env() {
    require_env_file
    validate_env_file_security

    postgres_password=$(env_value POSTGRES_PASSWORD)
    session_secret=$(env_value ENMOTION_SESSION_SECRET)
    [ -n "$postgres_password" ] || die "POSTGRES_PASSWORD is empty in $ENV_FILE"
    [ "$postgres_password" != "__GENERATE__" ] || die "POSTGRES_PASSWORD has not been generated"
    [ ${#session_secret} -ge 32 ] || die "ENMOTION_SESSION_SECRET must be at least 32 characters"
    [ "$session_secret" != "__GENERATE__" ] || die "ENMOTION_SESSION_SECRET has not been generated"

    case "$postgres_password" in
        *[!A-Za-z0-9._~-]*)
            die "POSTGRES_PASSWORD must be URL-safe (letters, numbers, '.', '_', '~', or '-')"
            ;;
    esac

    if [ "$DEPLOY_MODE" = production ]; then
        domain=$(env_value ENMOTION_DOMAIN)
        origins=$(env_value ENMOTION_ALLOWED_ORIGINS)
        bind_host=$(env_value ENMOTION_BIND_HOST)
        [ -n "$domain" ] || die "ENMOTION_DOMAIN is required in production"
        [ "$domain" != example.com ] || die "replace the example ENMOTION_DOMAIN before production"
        case "$domain" in
            http://*|https://*|*/*) die "ENMOTION_DOMAIN must be a bare DNS name" ;;
        esac
        case "$origins" in
            https://*) ;;
            *) die "ENMOTION_ALLOWED_ORIGINS must begin with https:// in production" ;;
        esac
        case "$bind_host" in
            127.*|localhost|::1|'[::1]'|'')
                die "ENMOTION_BIND_HOST must be an explicit non-loopback address in production (normally 0.0.0.0)"
                ;;
        esac
    fi
}

validate_env_file_security() {
    [ "$DEPLOY_MODE" = production ] || return 0
    [ ! -L "$ENV_FILE" ] || die "$ENV_FILE must not be a symbolic link in production"
    permissions=$(file_permissions "$ENV_FILE")
    case "$permissions" in
        600|400) ;;
        *) die "$ENV_FILE permissions are $permissions; production requires chmod 600 (or 400)" ;;
    esac
}

file_permissions() {
    stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null || printf unknown
}

require_private_file() {
    private_file=$1
    description=${2:-sensitive file}
    [ -f "$private_file" ] || die "$description is missing: $private_file"
    [ -s "$private_file" ] || die "$description is empty: $private_file"
    [ ! -L "$private_file" ] || die "$description must not be a symbolic link: $private_file"
    private_permissions=$(file_permissions "$private_file")
    case "$private_permissions" in
        600|400) ;;
        *) die "$description permissions are $private_permissions; use chmod 600 (or 400)" ;;
    esac
}

external_object_storage_in_use() {
    # Provider/runtime settings saved from the administrator UI live in the
    # app-data volume and override the initial container environment. Treat
    # either source enabling OSS as external state so backups never understate
    # their dependency on separately managed bucket objects.
    configured_external=false
    case "$(env_value OSS_ENABLE)" in
        true|TRUE|1|yes|YES|on|ON) configured_external=true ;;
    esac

    persisted_external=$(compose run --rm --no-deps --entrypoint python api -c '
import json
from pathlib import Path

path = Path("/data/config.json")
if not path.exists():
    print("unset")
else:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("runtime config must be a JSON object")
    value = raw.get("OSS_ENABLE")
    if value is None:
        print("unset")
    elif value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}:
        print("true")
    else:
        print("false")
') || die "could not inspect persisted OSS configuration"
    persisted_external=$(printf '%s\n' "$persisted_external" | awk 'NF { value = $0 } END { print value }')
    case "$persisted_external" in
        true) configured_external=true ;;
        false|unset|'') ;;
        *) die "persisted OSS configuration returned an invalid state" ;;
    esac

    printf '%s\n' "$configured_external"
}

sha256_file() {
    target=$1
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$target" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$target" | awk '{print $1}'
    else
        openssl dgst -sha256 "$target" | awk '{print $NF}'
    fi
}

utc_timestamp() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

file_timestamp() {
    date -u '+%Y%m%dT%H%M%SZ'
}

app_version() {
    tr -d '[:space:]' <"$DEPLOY_DIR/VERSION"
}

schema_version() {
    tr -d '[:space:]' <"$DEPLOY_DIR/SCHEMA_VERSION"
}

git_revision() {
    if command -v git >/dev/null 2>&1 && git -C "$APP_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "$APP_ROOT" rev-parse --verify HEAD 2>/dev/null || printf 'uncommitted\n'
    elif embedded_revision=$(embedded_source_identity_value source.revision 2>/dev/null) && \
            [ -n "$embedded_revision" ]; then
        printf '%s\n' "$embedded_revision"
    else
        printf 'not-a-git-checkout\n'
    fi
}

source_manifest_paths() {
    manifest_root=$1
    (
        cd "$manifest_root" || exit 1
        find . \
            \( \
                -path './.git' -o \
                -path './.venv' -o \
                -path './venv' -o \
                -path './node_modules' -o \
                -path './frontend/node_modules' -o \
                -path './frontend/.next' -o \
                -path './frontend/out' -o \
                -path './.mypy_cache' -o \
                -path './.pytest_cache' -o \
                -path './.ruff_cache' -o \
                -path './htmlcov' -o \
                -path './static' -o \
                -path './output' -o \
                -path './backups' -o \
                -path './transfers' -o \
                -path './data' -o \
                -path './.enmotion' -o \
                -path './logs' -o \
                -path './deploy/.lifecycle.lock' -o \
                -path './deploy/.export.lock' \
            \) -prune -o \
            \( -type f -o -type l \) -print | \
            sed 's#^\./##' | \
            awk '
                $0 == ".env" || $0 == "deploy/.env.server" ||
                    $0 == "frontend/.env.local" ||
                    $0 == "deploy/TRANSFER_PROVENANCE.txt" ||
                    $0 == "deploy/SOURCE_IDENTITY" ||
                    $0 == "deploy/SOURCE_MANIFEST.sha256" ||
                    $0 ~ /(^|\/)\.DS_Store$/ || $0 ~ /\.pyc$/ || $0 ~ /\.log$/ ||
                    $0 == ".coverage" { next }
                { print }
            ' | LC_ALL=C sort
    )
}

write_source_manifest() {
    manifest_root=$1
    manifest_output=$2
    manifest_list=$(mktemp "${TMPDIR:-/tmp}/enmotion-source-list.XXXXXX") || return 1
    if ! source_manifest_paths "$manifest_root" >"$manifest_list"; then
        rm -f -- "$manifest_list"
        return 1
    fi
    : >"$manifest_output" || {
        rm -f -- "$manifest_list"
        return 1
    }
    while IFS= read -r manifest_path; do
        [ -n "$manifest_path" ] || continue
        manifest_file=$manifest_root/$manifest_path
        # Symbolic links are not accepted because archive extractors differ in
        # how they materialize and permission them across supported hosts.
        if [ -L "$manifest_file" ] || [ ! -f "$manifest_file" ]; then
            rm -f -- "$manifest_list" "$manifest_output"
            return 1
        fi
        if ! manifest_digest=$(sha256_file "$manifest_file"); then
            rm -f -- "$manifest_list" "$manifest_output"
            return 1
        fi
        printf '%s  %s\n' "$manifest_digest" "$manifest_path" >>"$manifest_output" || {
            rm -f -- "$manifest_list" "$manifest_output"
            return 1
        }
    done <"$manifest_list"
    rm -f -- "$manifest_list"
}

write_git_source_manifest() {
    manifest_root=$1
    manifest_output=$2
    manifest_list=$(mktemp "${TMPDIR:-/tmp}/enmotion-git-source-list.XXXXXX") || return 1
    if ! git -C "$manifest_root" -c core.quotepath=false ls-files | \
            LC_ALL=C sort >"$manifest_list"; then
        rm -f -- "$manifest_list"
        return 1
    fi
    : >"$manifest_output" || {
        rm -f -- "$manifest_list"
        return 1
    }
    while IFS= read -r manifest_path; do
        [ -n "$manifest_path" ] || continue
        manifest_file=$manifest_root/$manifest_path
        if [ -L "$manifest_file" ] || [ ! -f "$manifest_file" ]; then
            rm -f -- "$manifest_list" "$manifest_output"
            return 1
        fi
        if ! manifest_digest=$(sha256_file "$manifest_file"); then
            rm -f -- "$manifest_list" "$manifest_output"
            return 1
        fi
        printf '%s  %s\n' "$manifest_digest" "$manifest_path" >>"$manifest_output" || {
            rm -f -- "$manifest_list" "$manifest_output"
            return 1
        }
    done <"$manifest_list"
    rm -f -- "$manifest_list"
}

embedded_source_identity_value() {
    identity_key=$1
    [ -f "$SOURCE_IDENTITY_FILE" ] && [ ! -L "$SOURCE_IDENTITY_FILE" ] || return 1
    identity_count=$(awk -F= -v key="$identity_key" '$1 == key { count++ } END { print count + 0 }' \
        "$SOURCE_IDENTITY_FILE")
    [ "$identity_count" -eq 1 ] || return 1
    awk -F= -v key="$identity_key" '$1 == key { print substr($0, index($0, "=") + 1) }' \
        "$SOURCE_IDENTITY_FILE"
}

embedded_source_identity_is_clean() {
    [ -f "$SOURCE_MANIFEST_FILE" ] && [ ! -L "$SOURCE_MANIFEST_FILE" ] || return 1
    identity_format=$(embedded_source_identity_value format.version 2>/dev/null) || return 1
    [ "$identity_format" = 1 ] || return 1
    expected_manifest_digest=$(embedded_source_identity_value source.manifest.sha256 2>/dev/null) || return 1
    case "$expected_manifest_digest" in
        *[!0-9A-Fa-f]*|'') return 1 ;;
    esac
    [ ${#expected_manifest_digest} -eq 64 ] || return 1
    actual_manifest_digest=$(sha256_file "$SOURCE_MANIFEST_FILE") || return 1
    [ "$actual_manifest_digest" = "$expected_manifest_digest" ] || return 1
    embedded_tree_identity=$(embedded_source_identity_value source.tree.identity 2>/dev/null) || return 1
    [ "$embedded_tree_identity" = "$expected_manifest_digest" ] || return 1

    current_manifest=$(mktemp "${TMPDIR:-/tmp}/enmotion-source-manifest.XXXXXX") || return 1
    if ! write_source_manifest "$APP_ROOT" "$current_manifest"; then
        rm -f -- "$current_manifest"
        return 1
    fi
    if cmp -s "$SOURCE_MANIFEST_FILE" "$current_manifest"; then
        rm -f -- "$current_manifest"
        return 0
    fi
    rm -f -- "$current_manifest"
    return 1
}

source_tree_state() {
    if command -v git >/dev/null 2>&1 && \
        git -C "$APP_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        if ! source_status=$(git -C "$APP_ROOT" status --porcelain --untracked-files=all 2>/dev/null); then
            printf 'unknown\n'
        elif [ -z "$source_status" ]; then
            printf 'clean\n'
        else
            printf 'dirty\n'
        fi
    elif embedded_source_identity_is_clean; then
        printf 'clean\n'
    elif [ -e "$SOURCE_IDENTITY_FILE" ] || [ -e "$SOURCE_MANIFEST_FILE" ]; then
        printf 'dirty\n'
    else
        printf 'unversioned\n'
    fi
}

source_tree_identity() {
    if command -v git >/dev/null 2>&1 && \
            git -C "$APP_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git_manifest=$(mktemp "${TMPDIR:-/tmp}/enmotion-git-manifest.XXXXXX") || {
            printf 'unknown\n'
            return 0
        }
        if write_git_source_manifest "$APP_ROOT" "$git_manifest"; then
            sha256_file "$git_manifest"
        else
            printf 'unknown\n'
        fi
        rm -f -- "$git_manifest"
        return 0
    fi
    if embedded_identity=$(embedded_source_identity_value source.tree.identity 2>/dev/null) && \
            [ -n "$embedded_identity" ]; then
        printf '%s\n' "$embedded_identity"
        return 0
    fi
    live_manifest=$(mktemp "${TMPDIR:-/tmp}/enmotion-live-manifest.XXXXXX") || {
        printf 'unknown\n'
        return 0
    }
    if write_source_manifest "$APP_ROOT" "$live_manifest"; then
        sha256_file "$live_manifest"
    else
        printf 'unknown\n'
    fi
    rm -f -- "$live_manifest"
}

deployment_version() {
    if [ -n "${ENMOTION_VERSION:-}" ]; then
        printf '%s\n' "$ENMOTION_VERSION"
        return 0
    fi
    if [ -f "$ENV_FILE" ]; then
        configured_version=$(env_value ENMOTION_VERSION)
        if [ -n "$configured_version" ]; then
            printf '%s\n' "$configured_version"
            return 0
        fi
    fi
    app_version
}

deployment_project_name() {
    if [ -n "${ENMOTION_COMPOSE_PROJECT_NAME:-}" ]; then
        printf '%s\n' "$ENMOTION_COMPOSE_PROJECT_NAME"
        return 0
    fi
    if [ -f "$ENV_FILE" ]; then
        configured_project=$(env_value ENMOTION_COMPOSE_PROJECT_NAME)
        if [ -n "$configured_project" ]; then
            printf '%s\n' "$configured_project"
            return 0
        fi
    fi
    printf 'enmotion-web\n'
}

prepare_image_build_provenance() {
    # Always derive revision and worktree state from the build context instead
    # of accepting possibly stale values from a caller's environment.
    if [ "$IMAGE_PROVENANCE_READY" = true ]; then
        return 0
    fi
    ENMOTION_VERSION=$(deployment_version)
    ENMOTION_SOURCE_REVISION=$(git_revision)
    ENMOTION_SOURCE_STATE=$(source_tree_state)
    ENMOTION_SOURCE_TREE_IDENTITY=$(source_tree_identity)
    ENMOTION_PYTHON_REQUIREMENTS_SHA256=unknown
    ENMOTION_JAVASCRIPT_LOCK_SHA256=unknown
    if [ -f "$APP_ROOT/requirements-docker.txt" ]; then
        ENMOTION_PYTHON_REQUIREMENTS_SHA256=$(sha256_file "$APP_ROOT/requirements-docker.txt")
    fi
    if [ -f "$APP_ROOT/frontend/package-lock.json" ]; then
        ENMOTION_JAVASCRIPT_LOCK_SHA256=$(sha256_file "$APP_ROOT/frontend/package-lock.json")
    fi
    export ENMOTION_VERSION ENMOTION_SOURCE_REVISION ENMOTION_SOURCE_STATE \
        ENMOTION_SOURCE_TREE_IDENTITY \
        ENMOTION_PYTHON_REQUIREMENTS_SHA256 ENMOTION_JAVASCRIPT_LOCK_SHA256
    IMAGE_PROVENANCE_READY=true
}

normalize_inspect_value() {
    case "$1" in
        ''|'<no value>'|'null') printf 'unknown\n' ;;
        *) printf '%s\n' "$1" ;;
    esac
}

check_running_image_provenance() {
    # Write an auditable report while checking both immutable container image
    # IDs and embedded source labels. Return 0 for verified, 1 for stale or
    # mismatched, 2 when no deployment containers run, and 3 when Docker state
    # cannot be observed.
    provenance_report=$1
    expected_revision=$2
    expected_state=$3
    expected_tree_identity=$4
    expected_version=$5
    project_name=$(deployment_project_name)
    observed_at=$(utc_timestamp)
    provenance_running=0
    provenance_services=0
    provenance_mismatches=0

    [ ! -L "$provenance_report" ] || die "provenance report must not be a symbolic link: $provenance_report"
    : >"$provenance_report"
    {
        printf 'format.version=1\n'
        printf 'observed.at=%s\n' "$observed_at"
        printf 'source.revision=%s\n' "$expected_revision"
        printf 'source.state=%s\n' "$expected_state"
        printf 'source.tree.identity=%s\n' "$expected_tree_identity"
        printf 'application.version=%s\n' "$expected_version"
        printf 'compose.project=%s\n' "$project_name"
    } >>"$provenance_report"

    if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
        printf 'running.images.status=docker-unavailable\n' >>"$provenance_report"
        return 3
    fi

    for provenance_service in database redis api worker frontend edge; do
        if ! container_ids=$(docker ps \
            --filter "label=com.docker.compose.project=$project_name" \
            --filter "label=com.docker.compose.service=$provenance_service" \
            --format '{{.ID}}' 2>/dev/null); then
            printf 'running.images.status=docker-unavailable\n' >>"$provenance_report"
            return 3
        fi
        [ -n "$container_ids" ] || continue
        provenance_services=$((provenance_services + 1))

        # The portable stack deliberately runs one container per service. More
        # than one is ambiguous, so record every identity but fail verification.
        # shellcheck disable=SC2086
        set -- $container_ids
        container_count=$#
        printf 'service.%s.container_count=%s\n' "$provenance_service" "$container_count" \
            >>"$provenance_report"
        [ "$container_count" -eq 1 ] || provenance_mismatches=$((provenance_mismatches + 1))

        for container_id in $container_ids; do
            provenance_running=$((provenance_running + 1))
            container_image_id=$(docker inspect --format '{{.Image}}' "$container_id" 2>/dev/null || true)
            configured_image=$(docker inspect --format '{{.Config.Image}}' "$container_id" 2>/dev/null || true)
            current_tag_id=$(docker image inspect --format '{{.Id}}' "$configured_image" 2>/dev/null || true)
            image_revision=$(docker image inspect \
                --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
                "$container_image_id" 2>/dev/null || true)
            image_state=$(docker image inspect \
                --format '{{index .Config.Labels "io.enmotion.source.state"}}' \
                "$container_image_id" 2>/dev/null || true)
            image_version=$(docker image inspect \
                --format '{{index .Config.Labels "org.opencontainers.image.version"}}' \
                "$container_image_id" 2>/dev/null || true)
            image_tree_identity=$(docker image inspect \
                --format '{{index .Config.Labels "io.enmotion.source.tree"}}' \
                "$container_image_id" 2>/dev/null || true)
            repo_digests=$(docker image inspect --format '{{json .RepoDigests}}' \
                "$container_image_id" 2>/dev/null || true)

            container_image_id=$(normalize_inspect_value "$container_image_id")
            configured_image=$(normalize_inspect_value "$configured_image")
            current_tag_id=$(normalize_inspect_value "$current_tag_id")
            image_revision=$(normalize_inspect_value "$image_revision")
            image_state=$(normalize_inspect_value "$image_state")
            image_version=$(normalize_inspect_value "$image_version")
            image_tree_identity=$(normalize_inspect_value "$image_tree_identity")
            repo_digests=$(normalize_inspect_value "$repo_digests")
            service_status=verified
            service_reasons=

            if [ "$container_image_id" != "$current_tag_id" ]; then
                service_status=stale
                service_reasons=tag-points-to-different-image
            fi
            if [ "$image_revision" != "$expected_revision" ]; then
                service_status=stale
                service_reasons=${service_reasons:+$service_reasons,}source-revision-mismatch
            fi
            if [ "$image_state" != "$expected_state" ]; then
                service_status=stale
                service_reasons=${service_reasons:+$service_reasons,}source-state-mismatch
            fi
            if [ "$image_tree_identity" != "$expected_tree_identity" ]; then
                service_status=stale
                service_reasons=${service_reasons:+$service_reasons,}source-tree-identity-mismatch
            fi
            if [ "$image_version" != "$expected_version" ]; then
                service_status=stale
                service_reasons=${service_reasons:+$service_reasons,}application-version-mismatch
            fi
            [ "$service_status" = verified ] || provenance_mismatches=$((provenance_mismatches + 1))

            {
                printf 'service.%s.container_id=%s\n' "$provenance_service" "$container_id"
                printf 'service.%s.container_image_id=%s\n' "$provenance_service" "$container_image_id"
                printf 'service.%s.configured_image=%s\n' "$provenance_service" "$configured_image"
                printf 'service.%s.current_tag_image_id=%s\n' "$provenance_service" "$current_tag_id"
                printf 'service.%s.repo_digests=%s\n' "$provenance_service" "$repo_digests"
                printf 'service.%s.source_revision=%s\n' "$provenance_service" "$image_revision"
                printf 'service.%s.source_state=%s\n' "$provenance_service" "$image_state"
                printf 'service.%s.source_tree_identity=%s\n' "$provenance_service" "$image_tree_identity"
                printf 'service.%s.application_version=%s\n' "$provenance_service" "$image_version"
                printf 'service.%s.status=%s\n' "$provenance_service" "$service_status"
                if [ -n "$service_reasons" ]; then
                    printf 'service.%s.reasons=%s\n' "$provenance_service" "$service_reasons"
                fi
            } >>"$provenance_report"
        done
    done

    printf 'running.images.observed_count=%s\n' "$provenance_running" >>"$provenance_report"
    printf 'running.services.observed_count=%s\n' "$provenance_services" >>"$provenance_report"
    printf 'running.services.expected_count=6\n' >>"$provenance_report"
    if [ "$provenance_running" -eq 0 ]; then
        printf 'running.images.status=not-running\n' >>"$provenance_report"
        return 2
    fi
    if [ "$provenance_services" -ne 6 ]; then
        printf 'running.images.status=partial\n' >>"$provenance_report"
        return 1
    fi
    if [ "$provenance_mismatches" -ne 0 ]; then
        printf 'running.images.status=stale\n' >>"$provenance_report"
        return 1
    fi
    printf 'running.images.status=verified\n' >>"$provenance_report"
    return 0
}

process_start_identity() {
    identity_pid=$1
    if [ -r "/proc/$identity_pid/stat" ]; then
        # Linux procfs field 22 is the process start tick since boot. Include
        # the current boot id so a reboot cannot make ticks ambiguous.
        start_ticks=$(sed 's/^.*) //' "/proc/$identity_pid/stat" 2>/dev/null | \
            awk '{ print $20 }' || true)
        boot_id=$(sed -n '1p' /proc/sys/kernel/random/boot_id 2>/dev/null || true)
        if [ -n "$start_ticks" ]; then
            printf 'proc:%s:%s\n' "${boot_id:-unknown-boot}" "$start_ticks"
            return 0
        fi
    fi
    start_text=$(ps -p "$identity_pid" -o lstart= 2>/dev/null | awk '{$1=$1; print}' || true)
    if [ -n "$start_text" ]; then
        printf 'ps:%s\n' "$start_text"
        return 0
    fi
    # Some constrained Mac environments deny ps(1). PID liveness is still a
    # safe fail-closed fallback: a reused PID may retain a stale lock, but a
    # live operation is never mistaken for stale and removed.
    if kill -0 "$identity_pid" 2>/dev/null; then
        printf 'pid-only\n'
    fi
}

acquire_lock() {
    lock_dir=$1
    lock_operation=${2:-deployment operation}
    lock_owner_file=$lock_dir/owner
    lock_pid=$$
    lock_start=$(process_start_identity "$lock_pid")
    [ -n "$lock_start" ] || die "could not determine deployment process identity"
    lock_token=$(random_hex 16)
    umask 077

    if ! mkdir "$lock_dir" 2>/dev/null; then
        # A creator can briefly exist between mkdir(2) and writing its owner
        # record. Give that tiny window time to close before judging staleness.
        [ -f "$lock_owner_file" ] || sleep 1
        owner_pid=$(sed -n 's/^pid=//p' "$lock_owner_file" 2>/dev/null | head -n 1 || true)
        owner_start=$(sed -n 's/^start=//p' "$lock_owner_file" 2>/dev/null | head -n 1 || true)
        owner_operation=$(sed -n 's/^operation=//p' "$lock_owner_file" 2>/dev/null | head -n 1 || true)
        current_start=
        case "$owner_pid" in
            ''|*[!0-9]*) ;;
            *) current_start=$(process_start_identity "$owner_pid") ;;
        esac
        if [ -n "$current_start" ] && [ "$current_start" = "$owner_start" ]; then
            die "another deployment operation (${owner_operation:-unknown}, pid $owner_pid) holds $lock_dir"
        fi

        warn "removing stale deployment lock left by pid ${owner_pid:-unknown}"
        rm -f -- "$lock_owner_file"
        rmdir "$lock_dir" 2>/dev/null || die "could not safely remove stale lock $lock_dir"
        mkdir "$lock_dir" 2>/dev/null || die "another deployment operation acquired $lock_dir"
    fi

    {
        printf 'pid=%s\n' "$lock_pid"
        printf 'start=%s\n' "$lock_start"
        printf 'token=%s\n' "$lock_token"
        printf 'operation=%s\n' "$lock_operation"
        printf 'created=%s\n' "$(utc_timestamp)"
    } >"$lock_owner_file"
    chmod 0600 "$lock_owner_file"
    ACTIVE_LOCK_DIR=$lock_dir
    ACTIVE_LOCK_TOKEN=$lock_token
}

release_lock() {
    lock_dir=$1
    lock_owner_file=$lock_dir/owner
    [ "${ACTIVE_LOCK_DIR:-}" = "$lock_dir" ] || return 0
    stored_pid=$(sed -n 's/^pid=//p' "$lock_owner_file" 2>/dev/null | head -n 1 || true)
    stored_token=$(sed -n 's/^token=//p' "$lock_owner_file" 2>/dev/null | head -n 1 || true)
    if [ "$stored_pid" = "$$" ] && [ "$stored_token" = "${ACTIVE_LOCK_TOKEN:-}" ]; then
        rm -f -- "$lock_owner_file"
        rmdir "$lock_dir" 2>/dev/null || true
    else
        warn "deployment lock ownership changed; leaving $lock_dir untouched"
    fi
    ACTIVE_LOCK_DIR=
    ACTIVE_LOCK_TOKEN=
}
