#!/usr/bin/env bash
# RHEL 9: Python 3.9+ standard library only. Keep the accompanying lib/data/assets.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'ERROR: python3 is required (RHEL 9: dnf install python3).' >&2
    exit 2
fi
exec python3 "$script_dir/lib/text_checker.py" "$@"
