#!/usr/bin/env bash
# Usage: install-code.sh <repo dir> <bundle> <commit> <uv> <wheelhouse> <python>
#
# Runs in the assistant's VM, as root: update.sh pushes a git bundle of <commit>, made on the
# devbox from its own checkout (make_bundle), uv, and a wheelhouse (a uv cache the devbox filled
# for this commit's lock and the VM's Python), then pipes this to `incus exec`. The VM reaches no
# package index or git host, so nothing here goes online (#91):
#
# - <commit> is fetched from the bundle, its branch `deploy`, and checked out in <repo dir>,
#   which is made on the first run. No remote is kept: the live VM's checkout was cloned from
#   GitHub, which it can't reach any more;
# - the venv is synced from the wheelhouse alone (UV_OFFLINE), on the VM's own <python>: uv never
#   downloads an interpreter;
# - the checkout and its venv are root's and read-only to the assistant (#92). It reads the
#   commit from a checkout that isn't its own (an eval run records it), so git trusts it.
#
# The bundle and the wheelhouse are removed afterwards, whether it worked or not.
set -euo pipefail
repo_dir=$1 bundle=$2 commit=$3 uv=$4 wheelhouse=$5 python=$6
umask 022
trap 'rm -rf "$bundle" "$wheelhouse"' EXIT

[[ -d $repo_dir/.git ]] || git init --quiet "$repo_dir"
cd "$repo_dir"
# make_bundle names the commit's branch `deploy`.
git fetch --quiet "$bundle" deploy
fetched=$(git rev-parse FETCH_HEAD)
if [[ $fetched != "$commit" ]]; then
  echo "error: the bundle holds $fetched, not $commit" >&2
  exit 1
fi
git checkout --quiet --force --detach "$commit"
for remote in $(git remote); do git remote remove "$remote"; done
git config --system --replace-all safe.directory "$repo_dir" "^$repo_dir\$"

UV_CACHE_DIR=$wheelhouse UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never \
  "$uv" sync --locked --no-dev --compile-bytecode --python "$python"
chmod -R go-w,a+rX "$repo_dir"

echo "deployed $(git rev-parse HEAD)"
