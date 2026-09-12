#!/usr/bin/env bash

set -Eeuo pipefail

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
default_data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
install_dir="$default_data_home/seek"
skip_system_dependencies=0
create_shortcuts=1
launch_after_install=1

usage() {
    cat <<'USAGE'
Usage: bash install-linux.sh [options]

Options:
  --install-dir PATH       Install SEEK in PATH.
  --skip-system-deps       Do not install Python, Tk, or FFmpeg packages.
  --no-shortcuts           Do not create the app-menu entry or seek command.
  --no-launch              Do not launch SEEK after installation.
  -h, --help               Show this help.
USAGE
}

die() {
    printf 'SEEK installer: %s\n' "$*" >&2
    exit 1
}

step() {
    printf '\n\033[1;36m==> %s\033[0m\n' "$*"
}

run_as_root() {
    if [[ "$EUID" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        die "sudo is required to install system packages."
    fi
}

system_requirements_ready() {
    command -v python3 >/dev/null 2>&1 &&
        python3 -c \
            'import sys; raise SystemExit(sys.version_info < (3, 10))' &&
        python3 -c 'import tkinter, venv, ensurepip' &&
        command -v ffmpeg >/dev/null 2>&1 &&
        command -v ffprobe >/dev/null 2>&1
}

install_system_dependencies() {
    if command -v apt-get >/dev/null 2>&1; then
        run_as_root apt-get update
        run_as_root env DEBIAN_FRONTEND=noninteractive \
            apt-get install -y python3 python3-venv python3-tk ffmpeg
    elif command -v dnf >/dev/null 2>&1; then
        local ffmpeg_package="ffmpeg"
        if dnf -q list ffmpeg-free >/dev/null 2>&1; then
            ffmpeg_package="ffmpeg-free"
        fi
        run_as_root dnf install -y \
            python3 python3-pip python3-tkinter "$ffmpeg_package"
    elif command -v yum >/dev/null 2>&1; then
        run_as_root yum install -y \
            python3 python3-pip python3-tkinter ffmpeg
    elif command -v pacman >/dev/null 2>&1; then
        run_as_root pacman -S --needed --noconfirm python tk ffmpeg
    elif command -v zypper >/dev/null 2>&1; then
        run_as_root zypper --non-interactive install \
            python3 python3-tk ffmpeg
    elif command -v apk >/dev/null 2>&1; then
        run_as_root apk add python3 py3-pip py3-virtualenv tk ffmpeg
    else
        die \
            "Unsupported package manager. Install Python 3.10+, Tk, " \
            "FFmpeg, and FFprobe, then rerun with --skip-system-deps."
    fi
}

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --install-dir)
            [[ "$#" -ge 2 ]] ||
                die "--install-dir requires a path."
            install_dir="$2"
            shift 2
            ;;
        --install-dir=*)
            install_dir="${1#*=}"
            shift
            ;;
        --skip-system-deps)
            skip_system_dependencies=1
            shift
            ;;
        --no-shortcuts)
            create_shortcuts=0
            shift
            ;;
        --no-launch)
            launch_after_install=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
done

[[ -n "$install_dir" ]] || die "The installation directory is empty."

required_source_files=(
    seek
    requirements.txt
    README.md
    install-linux.sh
    launch-linux.sh
)

for file in "${required_source_files[@]}"; do
    [[ -e "$source_dir/$file" ]] ||
        die "Required application file is missing: $source_dir/$file"
done

printf '\n\033[1;35mSEEK installer for Linux\033[0m\n'
printf '%s\n' '------------------------'

step "Checking Python 3.10+, Tk, and FFmpeg"
if ! system_requirements_ready; then
    if [[ "$skip_system_dependencies" -eq 1 ]]; then
        die \
            "A system requirement is missing. Install Python 3.10+, " \
            "the Python Tk/venv packages, FFmpeg, and FFprobe."
    fi

    step "Installing required system packages"
    install_system_dependencies
fi

system_requirements_ready ||
    die \
        "System requirements are still missing after package installation. " \
        "Confirm Python 3.10+, Tk, FFmpeg, and FFprobe are available."

step "Copying SEEK to $install_dir"
mkdir -p -- "$install_dir"
install_dir="$(cd -- "$install_dir" && pwd -P)"

if [[ "$source_dir" != "$install_dir" ]]; then
    for file in "${required_source_files[@]}"; do
        cp -af -- "$source_dir/$file" "$install_dir/$file"
    done
fi
chmod +x \
    "$install_dir/install-linux.sh" \
    "$install_dir/launch-linux.sh"

step "Creating the private Python environment"
venv_dir="$install_dir/.app-venv"
python3 -m venv "$venv_dir" ||
    die \
        "Could not create the virtual environment. Install your " \
        "distribution's python3-venv package and retry."

venv_python="$venv_dir/bin/python"
venv_pip="$venv_dir/bin/pip"

step "Installing SEEK's Python dependencies"
"$venv_python" -m pip install \
    --disable-pip-version-check \
    --upgrade pip
"$venv_pip" install \
    --disable-pip-version-check \
    -r "$install_dir/requirements.txt"

step "Running the post-install health check"
health_check="$(
    printf '%s' \
        "from seek.utils.system import check_dependencies; " \
        "r = check_dependencies(); " \
        "assert not r.missing_required, ', '.join(r.missing_required); " \
        "assert r.javascript_runtime, 'No JavaScript runtime found'; " \
        "print('JavaScript runtime:', r.javascript_runtime)"
)"
(
    cd -- "$install_dir"
    PATH="$venv_dir/bin:$PATH" \
        "$venv_python" -c "$health_check"
)

launcher_path="$install_dir/launch-linux.sh"
if [[ "$create_shortcuts" -eq 1 ]]; then
    step "Creating the app-menu entry and seek command"
    bin_dir="$HOME/.local/bin"
    command_path="$bin_dir/seek"
    mkdir -p -- "$bin_dir"

    if [[ -e "$command_path" && ! -L "$command_path" ]]; then
        printf '%s\n' \
            "Warning: $command_path already exists and was not replaced."
    else
        ln -sfn -- "$launcher_path" "$command_path"
    fi

    applications_dir="$default_data_home/applications"
    desktop_file="$applications_dir/seek-youtube-audio.desktop"
    mkdir -p -- "$applications_dir"
    desktop_exec="${launcher_path//\\/\\\\}"
    desktop_exec="${desktop_exec//\"/\\\"}"
    desktop_exec="${desktop_exec//\`/\\\`}"
    desktop_exec="${desktop_exec//\$/\\\$}"
    desktop_exec="${desktop_exec//%/%%}"

    printf '%s\n' \
        '[Desktop Entry]' \
        'Type=Application' \
        'Version=1.0' \
        'Name=SEEK' \
        'Comment=Save YouTube audio, thumbnails, and metadata' \
        "Exec=\"$desktop_exec\"" \
        'Icon=multimedia-player' \
        'Terminal=false' \
        'Categories=AudioVideo;Audio;Network;' \
        'StartupNotify=true' \
        >"$desktop_file"
    chmod 644 "$desktop_file"

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$applications_dir" >/dev/null 2>&1 ||
            true
    fi
fi

printf '\n\033[1;32mSEEK is installed.\033[0m\n'
printf 'Application: %s\n' "$install_dir"
printf 'Launcher:    %s\n' "$launcher_path"
printf '%s\n' \
    "Run this installer again at any time to update or repair SEEK."

if [[ "$launch_after_install" -eq 1 ]]; then
    if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
        step "Launching SEEK"
        nohup "$launcher_path" >/dev/null 2>&1 &
    else
        printf '%s\n' \
            "No graphical session was detected, so SEEK was not launched."
    fi
fi
