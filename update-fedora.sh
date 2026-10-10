#!/usr/bin/env bash
set -euo pipefail

# Update the installed app from a separate, managed Git checkout. The updater
# never changes the developer's checkout or the user's ATS folders/layouts.
REPO='https://github.com/Firefoxray/ATS-Controller-Manager.git'
APP_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/rayco-ats-controller-manager"
CHECKOUT="$APP_ROOT/upstream"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v git >/dev/null 2>&1; then
    echo 'Git is missing. Run: sudo dnf install git' >&2
    exit 1
fi
mkdir -p "$APP_ROOT"

if [[ ! -d "$CHECKOUT/.git" ]]; then
    if [[ -e "$CHECKOUT" ]]; then
        echo "Can't initialize updater: $CHECKOUT already exists but is not a Git repository." >&2
        exit 1
    fi
    echo 'Cloning the official project into the application data directory...'
    git clone --quiet --branch main --single-branch "$REPO" "$CHECKOUT"
else
    ORIGIN="$(git -C "$CHECKOUT" remote get-url origin)"
    case "$ORIGIN" in
        "$REPO"|https://github.com/Firefoxray/ATS-Controller-Manager|git@github.com:Firefoxray/ATS-Controller-Manager.git) ;;
        *) echo "Updater stopped: unexpected Git origin ($ORIGIN)" >&2; exit 1 ;;
    esac
    if [[ -n "$(git -C "$CHECKOUT" status --porcelain)" ]]; then
        echo 'Updater stopped: managed checkout has local changes; no files modified.' >&2
        exit 1
    fi
    if [[ "$(git -C "$CHECKOUT" branch --show-current)" != 'main' ]]; then
        echo 'Updater stopped: the managed checkout is not on main.' >&2
        exit 1
    fi
    echo 'Fetching updates from GitHub...'
    git -C "$CHECKOUT" fetch --quiet origin main
    git -C "$CHECKOUT" merge --quiet --ff-only FETCH_HEAD
fi

# Only install the app after a successful pull. Existing bindings and snapshots
# are deliberately outside the 'app' directory and never overwritten.
bash "$CHECKOUT/install-fedora.sh"
echo 'Controller Manager is up to date. ATS layouts and backups were not changed.'
