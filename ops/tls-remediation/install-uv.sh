#!/usr/bin/env bash
# F40 only: change the dedicated uv executable/link, preserving Node and PATH.
set -euo pipefail
artifacts=${1:?verified artifact directory required}
expected_old=${2:?exact observed prior uv link target required}
helper_dir=$(cd -- "$(dirname -- "$0")" && pwd)
python3 -B "$helper_dir/packages.py" verify "$artifacts"
test "$(id -u)" = 0
tools=/opt/biotact-team-tools
test -d "$tools/bin"
test ! -L "$tools"
test ! -L "$tools/bin"
test "$(stat -c %u:%g "$tools" "$tools/bin" | sort -u)" = 0:0
test -z "$(find "$tools" -maxdepth 1 -perm /022 -print)"
test -L "$tools/bin/uv"
test "$(readlink -f "$tools/bin/uv")" = "$expected_old"
test ! -e "$tools/uv-0.13.5"
install -o root -g root -m 0755 "$artifacts/uv" "$tools/uv-0.13.5"
printf '%s  %s\n' 8a70db72685b7138121bfcda71fa1a00cb709ed9f1fdc4ecf1dd871e8e167e52 "$tools/uv-0.13.5" | sha256sum --check --strict
link="$tools/bin/.uv-tls-$$"
test ! -e "$link"
ln -s ../uv-0.13.5 "$link"
mv -T "$link" "$tools/bin/uv"
test "$(readlink -f "$tools/bin/uv")" = "$tools/uv-0.13.5"
"$tools/bin/uv" --version
runuser -u biotact-team-deploy -- "$tools/bin/uv" --version
