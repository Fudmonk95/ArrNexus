#!/usr/bin/env bash
set -euo pipefail

REPO="Fudmonk95/ArrNexus"
BRANCH="feature/mediastack-v0.1"
SOURCE_TAR="${1:-/home/renegademonk/arrnexus-v13.4-live.tar.gz}"

if [[ ! -f "$SOURCE_TAR" ]]; then
  echo "Source archive not found: $SOURCE_TAR" >&2
  exit 1
fi

for cmd in git gh tar; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Required command is missing: $cmd" >&2
    exit 1
  fi
done

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not authenticated. Run: gh auth login" >&2
  exit 2
fi

gh auth setup-git >/dev/null

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/source"
tar -xzf "$SOURCE_TAR" -C "$WORK/source"
SOURCE_DIR="$WORK/source/arrnexus-v13.4-live"

if [[ ! -f "$SOURCE_DIR/VERSION" ]]; then
  echo "The archive does not contain VERSION at the expected path." >&2
  exit 3
fi

VERSION="$(tr -d '\r\n ' < "$SOURCE_DIR/VERSION")"
if [[ "$VERSION" != "13.4.0" ]]; then
  echo "Refusing to restore unexpected ArrNexus version: $VERSION" >&2
  exit 4
fi

gh repo clone "$REPO" "$WORK/repo" -- --quiet
cd "$WORK/repo"
git fetch origin "$BRANCH"
git checkout -B "$BRANCH" "origin/$BRANCH"

# Copy the application payload recovered from the running image. Do not remove
# branch-only files: MediaStack integration files may already exist and must
# survive this restore.
cp -a "$SOURCE_DIR/app/." app/
find app -type d -name '__pycache__' -prune -exec rm -rf {} +
find app -type f -name '*.pyc' -delete
cp "$SOURCE_DIR/VERSION" VERSION
cp "$SOURCE_DIR/requirements.txt" requirements.txt

# Never stage runtime data, databases or MediaStack secrets.
git add VERSION requirements.txt app/

if git diff --cached --quiet; then
  echo "No source differences to commit. The branch already contains v13.4.0."
  exit 0
fi

git config user.name "RenegadeMonk"
git config user.email "17593249+Fudmonk95@users.noreply.github.com"

echo
echo "Files being restored:"
git diff --cached --stat

git commit -m "Restore live ArrNexus v13.4.0 source baseline"
git push origin "$BRANCH"

echo
echo "Recovered ArrNexus v13.4.0 source pushed to $BRANCH."
echo "The running container was not modified."
