#!/usr/bin/env bash
set -euo pipefail

# Installs a per-user launcher; leaves Steam, ATS, and TruckNav unchanged.
if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  echo "Tkinter missing. Install it first: sudo dnf install python3-tkinter"
  exit 1
fi

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${XDG_DATA_HOME:-$HOME/.local/share}/rayco-ats-controller-manager/app"
mkdir -p "$TARGET" "$HOME/.local/share/applications" "$HOME/.local/bin"
install -m 755 "$SOURCE_DIR/ats_controller_manager.py" "$TARGET/ats_controller_manager.py"
install -m 755 "$SOURCE_DIR/update-fedora.sh" "$TARGET/update-fedora.sh"
cat > "$HOME/.local/bin/rayco-ats-controller-manager" <<EOF
#!/usr/bin/env bash
exec python3 "\${XDG_DATA_HOME:-\$HOME/.local/share}/rayco-ats-controller-manager/app/ats_controller_manager.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/rayco-ats-controller-manager"
cat > "$HOME/.local/share/applications/rayco-ats-controller-manager.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ray Co. ATS Controller Manager
Comment=Switch saved ATS controller configurations
Exec=$HOME/.local/bin/rayco-ats-controller-manager
Icon=input-gaming
Terminal=false
Categories=Game;Utility;
StartupNotify=true
EOF
command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true
printf 'Installed! Search for "Ray Co. ATS Controller Manager" in your KDE application menu.\n'
printf 'Or launch: %s/.local/bin/rayco-ats-controller-manager\n' "$HOME"
