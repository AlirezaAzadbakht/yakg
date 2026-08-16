#!/bin/sh
# Install the privileged yakg-helper and its polkit policy.
#
# The helper is the only process that reads or writes /etc/kerio-kvc.conf.
# It runs as root via pkexec, so this script copies a root-owned copy of the
# yakg package next to the wrapper. A user-writable editable install must
# never be what root imports.
#
# Source installation does not allow Save until this has been run once:
#
#     sudo ./packaging/install-helper.sh

set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (try: sudo $0)" >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SRC_PACKAGE="$REPO_ROOT/src/yakg"
POLICY_SRC="$REPO_ROOT/data/polkit/io.github.yakg.Yakg.policy"

if [ ! -f "$SRC_PACKAGE/helper.py" ]; then
    echo "Could not find src/yakg/helper.py next to this script." >&2
    exit 1
fi
if [ ! -f "$POLICY_SRC" ]; then
    echo "Could not find the polkit policy at $POLICY_SRC." >&2
    exit 1
fi

PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    echo "python3 is required to install the helper." >&2
    exit 1
fi
PYTHON=$(readlink -f "$PYTHON")

HELPER_DIR=/usr/libexec
LIB_DIR=/usr/libexec/yakg-lib
HELPER_PATH=/usr/libexec/yakg-helper
POLICY_DST=/usr/share/polkit-1/actions/io.github.yakg.Yakg.policy

install -d -m 0755 "$HELPER_DIR"
rm -rf "$LIB_DIR"
install -d -m 0755 "$LIB_DIR"
cp -a "$SRC_PACKAGE" "$LIB_DIR/yakg"
# Drop bytecode so a previous user's cache cannot run as root.
find "$LIB_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
chown -R root:root "$LIB_DIR"
find "$LIB_DIR" -type d -exec chmod 0755 {} \;
find "$LIB_DIR" -type f -exec chmod 0644 {} \;

# The shebang is the python3 that will import the root-owned copy.
cat > "$HELPER_PATH" <<EOF
#!${PYTHON}
import sys

sys.path.insert(0, "${LIB_DIR}")
from yakg.helper import main

if __name__ == "__main__":
    raise SystemExit(main())
EOF
chown root:root "$HELPER_PATH"
chmod 0755 "$HELPER_PATH"

install -d -m 0755 /usr/share/polkit-1/actions
install -m 0644 "$POLICY_SRC" "$POLICY_DST"

echo "Installed $HELPER_PATH and $POLICY_DST"
