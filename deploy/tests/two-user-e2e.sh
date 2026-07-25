#!/bin/sh
set -eu

base_url=${ENMOTION_TEST_BASE_URL:-http://127.0.0.1:8080}
admin_username=${ENMOTION_TEST_ADMIN_USERNAME:-}
admin_password=${ENMOTION_TEST_ADMIN_PASSWORD:-}
user_username=${ENMOTION_TEST_USER_USERNAME:-enmotion_test_user}
user_password=${ENMOTION_TEST_USER_PASSWORD:-}

[ -n "$admin_username" ] || { printf 'Set ENMOTION_TEST_ADMIN_USERNAME\n' >&2; exit 2; }
[ -n "$admin_password" ] || { printf 'Set ENMOTION_TEST_ADMIN_PASSWORD\n' >&2; exit 2; }
[ -n "$user_password" ] || { printf 'Set ENMOTION_TEST_USER_PASSWORD\n' >&2; exit 2; }

for command in curl jq mktemp; do
    command -v "$command" >/dev/null 2>&1 || {
        printf 'Missing required command: %s\n' "$command" >&2
        exit 2
    }
done

test_root=$(mktemp -d "${TMPDIR:-/tmp}/enmotion-e2e.XXXXXX")
cleanup() {
    rm -rf -- "$test_root"
}
trap cleanup EXIT INT TERM

admin_cookies=$test_root/admin.cookies
user_cookies=$test_root/user.cookies

login() {
    username=$1
    password=$2
    cookie_file=$3
    response_file=$4
    status=$(curl -sS -o "$response_file" -w '%{http_code}' \
        -c "$cookie_file" \
        -H 'Content-Type: application/json' \
        --data "$(jq -cn --arg username "$username" --arg password "$password" \
            '{username: $username, password: $password}')" \
        "$base_url/auth/login")
    [ "$status" = 200 ] || {
        printf 'Login for %s failed with HTTP %s: ' "$username" "$status" >&2
        cat "$response_file" >&2
        exit 1
    }
}

login "$admin_username" "$admin_password" "$admin_cookies" "$test_root/admin-login.json"
admin_csrf=$(jq -er '.csrf_token' "$test_root/admin-login.json")
admin_workspace=$(jq -er '.workspace_id' "$test_root/admin-login.json")

unauthenticated_status=$(curl -sS -o /dev/null -w '%{http_code}' "$base_url/projects")
[ "$unauthenticated_status" = 401 ] || {
    printf 'Protected project API returned HTTP %s without a login\n' \
        "$unauthenticated_status" >&2
    exit 1
}
missing_csrf_status=$(curl -sS -o /dev/null -w '%{http_code}' \
    -b "$admin_cookies" -H 'Content-Type: application/json' --data '{}' \
    "$base_url/auth/users")
[ "$missing_csrf_status" = 403 ] || {
    printf 'Mutation without CSRF token returned HTTP %s\n' "$missing_csrf_status" >&2
    exit 1
}

create_status=$(curl -sS -o "$test_root/create-user.json" -w '%{http_code}' \
    -b "$admin_cookies" \
    -H "X-CSRF-Token: $admin_csrf" \
    -H 'Content-Type: application/json' \
    --data "$(jq -cn \
        --arg username "$user_username" \
        --arg password "$user_password" \
        '{username: $username, password: $password, role: "user", workspace_name: "Portable test workspace", storage_quota_bytes: 1073741824}')" \
    "$base_url/auth/users")
case "$create_status" in
    201) ;;
    409) printf 'Test user already exists; reusing it for this isolation run\n' ;;
    *)
        printf 'Creating test user failed with HTTP %s: ' "$create_status" >&2
        cat "$test_root/create-user.json" >&2
        exit 1
        ;;
esac

login "$user_username" "$user_password" "$user_cookies" "$test_root/user-login.json"
user_csrf=$(jq -er '.csrf_token' "$test_root/user-login.json")
user_workspace=$(jq -er '.workspace_id' "$test_root/user-login.json")
[ "$admin_workspace" != "$user_workspace" ] || {
    printf 'Admin and test user unexpectedly share a workspace\n' >&2
    exit 1
}

create_project() {
    cookie_file=$1
    csrf=$2
    title=$3
    marker=$4
    output=$5
    status=$(curl -sS -o "$output" -w '%{http_code}' \
        -b "$cookie_file" \
        -H "X-CSRF-Token: $csrf" \
        -H 'Content-Type: application/json' \
        --data "$(jq -cn --arg title "$title" --arg text "$marker" \
            '{title: $title, text: $text, workflow_mode: "r2v"}')" \
        "$base_url/projects?skip_analysis=true")
    [ "$status" = 200 ] || {
        printf 'Creating project failed with HTTP %s: ' "$status" >&2
        cat "$output" >&2
        exit 1
    }
}

create_project "$admin_cookies" "$admin_csrf" \
    'Admin isolation sentinel' 'ADMIN_WORKSPACE_ONLY' "$test_root/admin-project.json"
create_project "$user_cookies" "$user_csrf" \
    'User isolation sentinel' 'USER_WORKSPACE_ONLY' "$test_root/user-project.json"
admin_project=$(jq -er '.id' "$test_root/admin-project.json")
user_project=$(jq -er '.id' "$test_root/user-project.json")

admin_cross=$(curl -sS -o /dev/null -w '%{http_code}' \
    -b "$admin_cookies" "$base_url/projects/$user_project")
user_cross=$(curl -sS -o /dev/null -w '%{http_code}' \
    -b "$user_cookies" "$base_url/projects/$admin_project")
[ "$admin_cross" = 404 ] && [ "$user_cross" = 404 ] || {
    printf 'Cross-workspace project isolation failed (admin=%s user=%s)\n' \
        "$admin_cross" "$user_cross" >&2
    exit 1
}

printf 'ADMIN_PRIVATE_MEDIA' >"$test_root/admin.png"
upload_status=$(curl -sS -o "$test_root/admin-upload.json" -w '%{http_code}' \
    -b "$admin_cookies" \
    -H "X-CSRF-Token: $admin_csrf" \
    -F "file=@$test_root/admin.png;type=image/png" \
    "$base_url/upload")
[ "$upload_status" = 200 ] || {
    printf 'Admin media upload failed with HTTP %s\n' "$upload_status" >&2
    exit 1
}
admin_media=$(jq -er '.url' "$test_root/admin-upload.json")
curl -fsS -b "$admin_cookies" "$base_url/files/$admin_media" \
    -o "$test_root/admin-media-result"
grep -q 'ADMIN_PRIVATE_MEDIA' "$test_root/admin-media-result"
media_cross=$(curl -sS -o /dev/null -w '%{http_code}' \
    -b "$user_cookies" "$base_url/files/$admin_media")
[ "$media_cross" = 404 ] || {
    printf 'Cross-workspace media isolation failed with HTTP %s\n' "$media_cross" >&2
    exit 1
}

admin_header=$(curl -sS -D - -o /dev/null -b "$admin_cookies" "$base_url/projects" | \
    tr -d '\r' | awk -F ': ' 'tolower($1) == "x-enmotion-workspace-id" { print $2 }')
[ "$admin_header" = "$admin_workspace" ] || {
    printf 'Workspace response header did not match the authenticated workspace\n' >&2
    exit 1
}

curl -fsS -b "$admin_cookies" "$base_url/jobs" | jq -e 'type == "array"' >/dev/null
curl -fsS -b "$user_cookies" "$base_url/jobs" | jq -e 'type == "array"' >/dev/null

jq -n \
    --arg admin_workspace "$admin_workspace" \
    --arg user_workspace "$user_workspace" \
    --arg admin_project "$admin_project" \
    --arg user_project "$user_project" \
    --arg admin_media "$admin_media" \
    '{status: "passed", admin_workspace: $admin_workspace, user_workspace: $user_workspace, admin_project: $admin_project, user_project: $user_project, admin_media: $admin_media}'
