#!/usr/bin/env bash
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# ArrNexus historical GitHub publisher
#
# Modes:
#   ./publish_arrnexus_history.sh --prepare
#       Rebuilds a CLEAN local Git history from the version snapshot folders.
#       Runs privacy/secret scans BEFORE every commit.
#       NEVER pushes to GitHub.
#
#   ./publish_arrnexus_history.sh --scan-history
#       Re-scans every committed revision in the prepared repository.
#
#   ./publish_arrnexus_history.sh --push
#       Re-scans the complete history, verifies the remote is empty, then
#       pushes main + all version tags.
#
# Original source directories are READ-ONLY to this script.
# ---------------------------------------------------------------------------

MODE="${1:---prepare}"

SOURCE_ROOT="${ARRNEXUS_SOURCE_ROOT:-/opt/dmm-arr-router}"
WORK_ROOT="${ARRNEXUS_GIT_WORKDIR:-${SOURCE_ROOT}/arrnexus-github-publish}"
TOOLKIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Public repository. Override if necessary:
#   ARRNEXUS_REMOTE_URL=https://github.com/<owner>/ArrNexus.git ./publish... --prepare
REMOTE_URL="${ARRNEXUS_REMOTE_URL:-https://github.com/Fudmonk95/ArrNexus.git}"

REPORT_DIR="${WORK_ROOT}.reports"
PREPARE_REPORT="${REPORT_DIR}/prepare-scan.txt"
HISTORY_REPORT="${REPORT_DIR}/history-scan.txt"

VERSIONS=(
  "v0.2|dmm-arr-router-v0.2"
  "v1.0|dmm-arr-router-v1.0"
  "v2.0|arrnexus-v2.0"
  "v3.0|arrnexus-v3.0-validated"
  "v4.0|arrnexus-v4.0"
  "v5.0|arrnexus-v5.0"
  "v6.0|arrnexus-v6.0"
  "v6.1|arrnexus-v6.1"
  "v7.0.0-beta|arrnexus-v7.0"
)

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '\n==> %s\n' "$*"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

check_prereqs() {
  need_cmd git
  need_cmd rsync
  need_cmd python3
  need_cmd find
  need_cmd sed
  need_cmd awk

  [[ -d "$SOURCE_ROOT" ]] || die "Source root does not exist: $SOURCE_ROOT"

  for item in "${VERSIONS[@]}"; do
    IFS='|' read -r tag dir <<<"$item"
    [[ -d "${SOURCE_ROOT}/${dir}" ]] || die "Missing source directory for ${tag}: ${SOURCE_ROOT}/${dir}"
  done

  for f in README.md .gitignore SECURITY.md; do
    [[ -f "${TOOLKIT_DIR}/${f}" ]] || die "Toolkit file missing beside script: ${f}"
  done
}

# Copies one historical source snapshot to WORK_ROOT.
# Known deployment/runtime state is excluded before scanning/committing.
copy_snapshot() {
  local source_dir="$1"

  # WORK_ROOT is dedicated and checked before deletion.
  [[ "$(basename "$WORK_ROOT")" == "arrnexus-github-publish" ]] \
    || die "Refusing to clean unexpected work directory: $WORK_ROOT"

  mkdir -p "$WORK_ROOT"

  find "$WORK_ROOT" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf -- {} +

  rsync -a \
    --exclude='.git/' \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='*.db' \
    --exclude='*.db-*' \
    --exclude='*.sqlite' \
    --exclude='*.sqlite3' \
    --exclude='*.sqlite-*' \
    --exclude='*.log' \
    --exclude='__pycache__/' \
    --exclude='.pytest_cache/' \
    --exclude='.mypy_cache/' \
    --exclude='.ruff_cache/' \
    --exclude='.venv/' \
    --exclude='venv/' \
    --exclude='node_modules/' \
    --exclude='/data/' \
    --exclude='/logs/' \
    --exclude='/cache/' \
    --exclude='/caches/' \
    --exclude='/backups/' \
    --exclude='/downloads/' \
    --exclude='/tmp/' \
    --exclude='/temp/' \
    "${source_dir}/" "${WORK_ROOT}/"
}

# Scanner intentionally reports PATH + LINE + CATEGORY only.
# It does not print the suspected secret value into the report.
scan_tree() {
  local scan_root="$1"
  local label="$2"
  local report="$3"

  python3 - "$scan_root" "$label" "$report" <<'PY'
from pathlib import Path
import os, re, sys

root = Path(sys.argv[1]).resolve()
label = sys.argv[2]
report_path = Path(sys.argv[3])

skip_dirs = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache"
}
skip_suffixes = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".mkv",
    ".zip", ".gz", ".tgz", ".tar", ".7z", ".rar", ".db", ".sqlite",
    ".sqlite3", ".pyc"
}

private_ipv4 = re.compile(
    r"(?<!\d)(?:"
    r"10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r")(?!\d)"
)
unix_user_path = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+/")
windows_user_path = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\r\n\t ]+\\")
url_credentials = re.compile(r"(?i)https?://[^/\s:@]+:[^@\s/]+@")
bearer = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")
github_token = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
google_key = re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")
aws_key = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

secret_literal = re.compile(
    r"""(?ix)
    \b(
        api[_-]?key|apikey|token|password|passwd|secret|
        client[_-]?secret|refresh[_-]?token|access[_-]?token|
        authorization
    )\b
    \s*["']?\s*[:=]\s*
    ["']
    ([^"'\r\n]{8,})
    ["']
    """
)

safe_markers = (
    "<your_", "<api_", "<token", "<secret", "<password",
    "${", "{{", "changeme", "change_me", "placeholder",
    "example", "redacted", "xxxxxxxx", "********", "dummy",
    "test-token", "test_token", "your-api", "your_api",
    "none", "null"
)

findings = []

def add(category, rel, line_no):
    findings.append((category, str(rel), int(line_no)))

def line_of(text, pos):
    return text.count("\n", 0, pos) + 1

for p in root.rglob("*"):
    try:
        rel = p.relative_to(root)
    except Exception:
        continue

    if any(part in skip_dirs for part in rel.parts):
        continue

    if p.is_symlink():
        try:
            target = os.readlink(p)
        except OSError:
            target = ""
        # Absolute symlinks can leak local deployment paths or point outside repo.
        if os.path.isabs(target):
            add("ABSOLUTE_SYMLINK", rel, 1)
        if unix_user_path.search(target):
            add("USERNAME_PATH_IN_SYMLINK", rel, 1)
        if private_ipv4.search(target):
            add("PRIVATE_IP_IN_SYMLINK", rel, 1)
        continue

    if not p.is_file():
        continue
    if p.suffix.lower() in skip_suffixes:
        continue
    try:
        if p.stat().st_size > 5 * 1024 * 1024:
            continue
        raw = p.read_bytes()
    except Exception:
        continue
    if b"\x00" in raw:
        continue

    text = raw.decode("utf-8", errors="replace")

    for m in private_ipv4.finditer(text):
        add("PRIVATE_IPV4", rel, line_of(text, m.start()))
    for m in unix_user_path.finditer(text):
        # /home/<user>/ and /Users/<user>/ are treated as deployment-specific.
        add("LOCAL_USERNAME_PATH", rel, line_of(text, m.start()))
    for m in windows_user_path.finditer(text):
        add("WINDOWS_USERNAME_PATH", rel, line_of(text, m.start()))
    for m in url_credentials.finditer(text):
        add("CREDENTIALS_IN_URL", rel, line_of(text, m.start()))
    for m in bearer.finditer(text):
        token_text = m.group(0).lower()
        if not any(marker in token_text for marker in safe_markers):
            add("BEARER_TOKEN", rel, line_of(text, m.start()))
    for regex, category in [
        (github_token, "GITHUB_TOKEN"),
        (google_key, "GOOGLE_API_KEY"),
        (aws_key, "AWS_ACCESS_KEY"),
    ]:
        for m in regex.finditer(text):
            add(category, rel, line_of(text, m.start()))

    for m in secret_literal.finditer(text):
        value = m.group(2).strip().lower()
        if any(marker in value for marker in safe_markers):
            continue
        # Avoid obvious code-expression examples captured as strings.
        if value.startswith(("http://example.", "https://example.")):
            continue
        add("HARDCODED_SECRET_LITERAL", rel, line_of(text, m.start()))

report_path.parent.mkdir(parents=True, exist_ok=True)
with report_path.open("a", encoding="utf-8") as fh:
    fh.write(f"\n===== {label} =====\n")
    if not findings:
        fh.write("PASS: no scanner findings\n")
    else:
        for category, rel, line_no in sorted(set(findings)):
            fh.write(f"{category}\t{rel}\tline {line_no}\n")

if findings:
    print(f"SCAN_BLOCKED {label}: {len(set(findings))} finding(s)")
    sys.exit(42)

print(f"SCAN_PASS {label}")
PY
}

scan_commit_history() {
  mkdir -p "$REPORT_DIR"
  : > "$HISTORY_REPORT"

  info "Re-scanning every Git revision"

  local failed=0
  local temp
  temp="$(mktemp -d)"
  trap 'rm -rf "$temp"' RETURN

  while read -r commit; do
    rm -rf "$temp/tree"
    mkdir -p "$temp/tree"
    git -C "$WORK_ROOT" archive "$commit" | tar -x -C "$temp/tree"
    label="$(git -C "$WORK_ROOT" show -s --format='%h %s' "$commit")"

    if ! scan_tree "$temp/tree" "COMMIT ${label}" "$HISTORY_REPORT"; then
      failed=1
    fi
  done < <(git -C "$WORK_ROOT" rev-list --reverse main)

  rm -rf "$temp"
  trap - RETURN

  if [[ "$failed" -ne 0 ]]; then
    printf '\nHistory scan BLOCKED.\n'
    printf 'Review: %s\n' "$HISTORY_REPORT"
    return 42
  fi

  # If gitleaks is available, run an additional Git-history scan.
  if command -v gitleaks >/dev/null 2>&1; then
    info "Running additional gitleaks scan"
    if ! gitleaks git "$WORK_ROOT" --redact --no-banner; then
      die "gitleaks found one or more potential secrets. Nothing has been pushed."
    fi
  else
    printf '\nNOTE: gitleaks is not installed; built-in privacy/secret scanner was used.\n'
  fi

  info "Complete history scan passed"
  printf 'Report: %s\n' "$HISTORY_REPORT"
}

prepare_history() {
  check_prereqs

  info "Preparing ArrNexus historical repository"
  printf 'Source root : %s\n' "$SOURCE_ROOT"
  printf 'Work repo   : %s\n' "$WORK_ROOT"
  printf 'Reports     : %s\n' "$REPORT_DIR"
  printf 'Remote      : %s\n' "$REMOTE_URL"

  [[ "$WORK_ROOT" != "$SOURCE_ROOT" ]] || die "Work directory cannot equal source root."
  [[ "$WORK_ROOT" != "${SOURCE_ROOT}/arrnexus-v7.0" ]] || die "Unsafe work directory."

  rm -rf "$WORK_ROOT" "$REPORT_DIR"
  mkdir -p "$WORK_ROOT" "$REPORT_DIR"
  : > "$PREPARE_REPORT"

  git -C "$WORK_ROOT" init -b main

  local git_name git_email
  git_name="$(git config --global user.name 2>/dev/null || true)"
  git_email="$(git config --global user.email 2>/dev/null || true)"

  if [[ -z "$git_name" ]]; then
    read -r -p "Git commit display name: " git_name
  fi
  if [[ -z "$git_email" ]]; then
    read -r -p "Git commit email (GitHub noreply address recommended): " git_email
  fi

  [[ -n "$git_name" ]] || die "Git display name cannot be empty."
  [[ -n "$git_email" ]] || die "Git email cannot be empty."

  git -C "$WORK_ROOT" config user.name "$git_name"
  git -C "$WORK_ROOT" config user.email "$git_email"
  git -C "$WORK_ROOT" remote add origin "$REMOTE_URL"

  local tag dir src
  for item in "${VERSIONS[@]}"; do
    IFS='|' read -r tag dir <<<"$item"
    src="${SOURCE_ROOT}/${dir}"

    info "Importing ${tag} from ${dir}"
    copy_snapshot "$src"

    # The active v7 public branch gets the main public documentation and guards.
    if [[ "$tag" == "v7.0.0-beta" ]]; then
      mkdir -p "$WORK_ROOT"
      cp "${TOOLKIT_DIR}/README.md" "$WORK_ROOT/README.md"
      cp "${TOOLKIT_DIR}/.gitignore" "$WORK_ROOT/.gitignore"
      cp "${TOOLKIT_DIR}/SECURITY.md" "$WORK_ROOT/SECURITY.md"
    fi

    # Reject nested Git repositories that slipped through.
    if find "$WORK_ROOT" -mindepth 2 -name .git -print -quit | grep -q .; then
      die "Nested .git directory detected in ${tag}. Nothing has been pushed."
    fi

    if ! scan_tree "$WORK_ROOT" "SNAPSHOT ${tag}" "$PREPARE_REPORT"; then
      printf '\nPRIVACY/SECRET SCAN BLOCKED %s.\n' "$tag"
      printf 'Nothing has been pushed and the ORIGINAL source folders were not changed.\n'
      printf 'Review the redacted findings report:\n  %s\n' "$PREPARE_REPORT"
      exit 42
    fi

    git -C "$WORK_ROOT" add -A

    if git -C "$WORK_ROOT" diff --cached --quiet; then
      die "No files staged for ${tag}; refusing to create an empty historical snapshot."
    fi

    if [[ "$tag" == "v7.0.0-beta" ]]; then
      git -C "$WORK_ROOT" commit -m "ArrNexus v7.0 beta"
      git -C "$WORK_ROOT" tag -a "$tag" -m "ArrNexus v7.0 beta"
    else
      git -C "$WORK_ROOT" commit -m "Historical snapshot: ${tag}"
      git -C "$WORK_ROOT" tag -a "$tag" -m "ArrNexus ${tag}"
    fi
  done

  scan_commit_history

  info "Prepared repository successfully - NO PUSH HAS OCCURRED"
  printf '\nVersion history:\n'
  git -C "$WORK_ROOT" --no-pager log --oneline --decorate --graph --all

  printf '\nTags:\n'
  git -C "$WORK_ROOT" tag --list --sort=version:refname

  printf '\nTracked file count: '
  git -C "$WORK_ROOT" ls-files | wc -l

  printf '\nRepository size:\n'
  git -C "$WORK_ROOT" count-objects -vH

  printf '\nReports:\n'
  printf '  %s\n' "$PREPARE_REPORT"
  printf '  %s\n' "$HISTORY_REPORT"

  cat <<EOF

SAFE STOP REACHED.

Nothing has been uploaded to GitHub.

Before pushing, review the two reports above. If they both contain only PASS
results, run:

  ${TOOLKIT_DIR}/publish_arrnexus_history.sh --push

If the scan is blocked, DO NOT PUSH. Review the reported file/line locations
and sanitise a COPY of the affected historical source before rebuilding.
EOF
}

push_history() {
  check_prereqs
  [[ -d "$WORK_ROOT/.git" ]] || die "Prepared repository not found. Run --prepare first."

  scan_commit_history

  info "Checking GitHub remote before first push"

  # The intended remote was created empty. Do not merge unrelated GitHub
  # starter commits into the reconstructed history by accident.
  remote_refs="$(git ls-remote "$REMOTE_URL" 2>/dev/null || true)"
  if [[ -n "$remote_refs" ]]; then
    cat >&2 <<EOF

The GitHub remote is NOT empty.

For safety this script will not overwrite or merge unrelated remote history.
If you added a README/license manually on GitHub, remove/recreate the empty
repository or handle that commit deliberately before continuing.

Remote:
  $REMOTE_URL

Nothing has been pushed.
EOF
    exit 43
  fi

  printf '\nREADY TO PUBLISH:\n'
  printf '  remote: %s\n' "$REMOTE_URL"
  printf '  branch: main\n'
  printf '  tags  : '
  git -C "$WORK_ROOT" tag --list --sort=version:refname | paste -sd ', ' -
  printf '\n'

  read -r -p 'Type exactly "PUSH ARRNEXUS" to upload main and all tags: ' confirmation
  [[ "$confirmation" == "PUSH ARRNEXUS" ]] || die "Push cancelled."

  info "Pushing main"
  git -C "$WORK_ROOT" push -u origin main

  info "Pushing version tags"
  git -C "$WORK_ROOT" push origin --tags

  info "GitHub publication complete"
  printf '\nThe repository now contains the reconstructed historical snapshots and v7 beta on main.\n'
}

case "$MODE" in
  --prepare)
    prepare_history
    ;;
  --scan-history)
    check_prereqs
    [[ -d "$WORK_ROOT/.git" ]] || die "Prepared repository not found. Run --prepare first."
    scan_commit_history
    ;;
  --push)
    push_history
    ;;
  *)
    cat >&2 <<EOF
Usage:
  $0 --prepare
  $0 --scan-history
  $0 --push

Optional environment variables:
  ARRNEXUS_SOURCE_ROOT=/opt/dmm-arr-router
  ARRNEXUS_GIT_WORKDIR=/opt/dmm-arr-router/arrnexus-github-publish
  ARRNEXUS_REMOTE_URL=https://github.com/<owner>/ArrNexus.git
EOF
    exit 2
    ;;
esac
