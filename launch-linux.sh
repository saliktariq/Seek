#!/usr/bin/env bash

set -Eeuo pipefail

app_dir="$(cd -- "$(dirname -- "$(realpath "${BASH_SOURCE[0]}")")" && pwd -P)"
venv_bin="$app_dir/.app-venv/bin"
python_path="$venv_bin/python"

if [[ ! -x "$python_path" ]]; then
    printf '%s\n' \
        "SEEK is not installed. Run install-linux.sh first." >&2
    exit 1
fi

export PATH="$venv_bin:$PATH"
cd -- "$app_dir"
exec "$python_path" -m seek
