#!/usr/bin/env bash
# F40 only. Authenticated local .deb; no apt update or automatic service restart.
set -euo pipefail
artifacts=${1:?reviewed artifact directory required}
receipt=${2:?new root-private receipt directory required}
helper_dir=$(cd -- "$(dirname -- "$0")" && pwd)
test "$(id -u)" = 0
test ! -e "$receipt"
install -d -m 0700 "$receipt"
python3 -B "$helper_dir/packages.py" verify "$artifacts"
sha256sum --check --strict "$artifacts/apt-inputs.sha256"
test -z "$(find /etc/ca-certificates/update.d -mindepth 1 -maxdepth 1 -print)"
test -z "$(find /usr/local/share/ca-certificates -name '*.crt' -print)"
test -z "$(dpkg --audit)"
dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' > "$receipt/packages.before"
cp -p /etc/ca-certificates.conf "$receipt/selection.before"
# APT 2.8.3 --no-download needs the verified .deb already in its archive cache.
# Keep this invocation's cache private and absolute; never touch the shared cache.
receipt=$(cd -- "$receipt" && pwd -P)
archive="$receipt/apt-archives"
ca_deb=ca-certificates_20260816ubuntu0.24.04.1_all.deb
install -d -m 0700 "$archive"
install -m 0444 "$artifacts/$ca_deb" "$archive/$ca_deb"
printf '%s  %s\n' 8a0a754aaafed72cc8efb8c1731808133284a84fbfc76a4fcc73fce9e01b4187 \
  "$archive/$ca_deb" | sha256sum --check --strict
# List-only needrestart overrides Ubuntu's automatic APT-hook default for THIS invocation.
NEEDRESTART_MODE=l apt-get --simulate --no-download --only-upgrade \
  -o "Dir::Cache::Archives=$archive" install "$artifacts/$ca_deb" > "$receipt/apt-simulation.log" 2>&1
python3 -B "$helper_dir/packages.py" apt-simulation "$receipt/apt-simulation.log"
NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get --yes --no-download --only-upgrade \
  -o "Dir::Cache::Archives=$archive" install "$artifacts/$ca_deb" > "$receipt/apt-install.log" 2>&1
test -z "$(dpkg --audit)"
test "$(dpkg-query -W -f='${Version}' ca-certificates)" = 20260816ubuntu0.24.04.1
test -z "$(dpkg-query -W -f='${Triggers-Pending}${Triggers-Awaited}' ca-certificates)"
dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' > "$receipt/packages.after"
python3 -B - "$receipt" <<'PY'
import sys
from pathlib import Path
receipt=Path(sys.argv[1])
def rows(name):
    return {line.split('\t')[0]:line.split('\t')[1:] for line in (receipt/name).read_text().splitlines()}
before,after=rows('packages.before'),rows('packages.after')
expected=dict(before)
expected['ca-certificates']=['20260816ubuntu0.24.04.1','ii ']
if after!=expected:raise SystemExit('unexpected complete dpkg map delta; stop')
old={line for line in (receipt/'selection.before').read_text().splitlines() if line.startswith('!')}
new=set(Path('/etc/ca-certificates.conf').read_text().splitlines())
if not old<=new:raise SystemExit('explicit CA deselection changed; stop')
PY
# Required next: inspect triggers/log, prove unchanged consumer identities and run
# the default/system-linked trust matrix. This script never starts the next batch.
