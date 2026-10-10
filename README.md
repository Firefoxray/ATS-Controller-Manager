# Ray Co. ATS Controller Manager

**Version 0.3.0 — Fedora KDE / ATS (Proton, WineLand, or native Linux)**

A dark Tkinter launcher to save and restore **American Truck Simulator's own controller bindings** without rewriting your favorite setup whenever you switch from DualSense to the original Steam Controller or Xbox pad. Inspired by the Ray Co. TruckNav launcher, but an independent project.

> **Beta / local test build.** It has been tested against simulated ATS profiles, not yet against your real WineLand prefix. Quit ATS before saving or applying anything. Make a copy of important profiles first.

## Install on Fedora

```bash
sudo dnf install -y git python3-tkinter
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/Firefoxray/ATS-Controller-Manager.git
cd ATS-Controller-Manager
bash ./install-fedora.sh
```

Open **Ray Co. ATS Controller Manager** from the KDE application menu or launch with:

```bash
rayco-ats-controller-manager
```

If `~/.local/bin` is not on `PATH`, launch using `~/.local/bin/rayco-ats-controller-manager` instead. There is **no compile/build step**; Python 3 and Tkinter run directly.

## How to use

1. **Close ATS completely.** Check the detected profile at the top of the window. Proton/WineLand profiles are listed first; use **Browse** if yours is missing.
2. After configuring your DualSense in ATS, **exit the game** and press **Save Current Layout**. Name it `DualSense`.
3. Start ATS with your Steam Controller, configure it in the game, quit, and save a new layout named `Steam Controller`.
4. With ATS closed, select one of your saved layouts and press **Apply Selected Layout**. A pre-switch backup is always created.
5. If necessary, **Undo Last Switch** restores the last pre-switch snapshot. You can view all snapshots and backups in the app's data folder.

**Default:** `config_local.cfg` is *not* included, to avoid accidentally swapping unrelated per-profile game settings. Tick the checkbox if your specific controller layout needs it. Existing saved snapshots can include this file if the box was checked while saving.

## Update button

The **Check / Update From GitHub** button in the GUI:

- Confirms before starting and retrieves the latest `main` branch from this repository.
- Uses an isolated clone at `~/.local/share/rayco-ats-controller-manager/upstream`, so your `~/Projects` checkout is never modified.
- Only reinstalls the application scripts and launcher. **Controller snapshots, backups, ATS profiles, game saves, mods and Steam Input layouts are untouched.**
- Reports failures inside the app; details are saved in `~/.local/share/rayco-ats-controller-manager/update.log`.
- Offers to restart the GUI after an update.

You can also manually update your development checkout and reinstall:

```bash
cd ~/Projects/ATS-Controller-Manager
git pull --ff-only
bash ./install-fedora.sh
```

## WineLand / Proton support

ATS is Steam **app ID 270880**. WineLand, like other Steam compatibility tools, normally uses ATS's Proton prefix; its name doesn't require a special directory. For example:

```text
~/.local/share/Steam/steamapps/compatdata/270880/pfx/drive_c/users/steamuser/Documents/American Truck Simulator/steam_profiles/<profile-id>/controls.sii
```

The manager finds Steam libraries from `libraryfolders.vdf`, checks other Windows user folder names in prefixes, supports `STEAM_COMPAT_DATA_PATH`, and searches native Linux ATS as a fallback. For unrecognized or custom prefixes, choose **Browse** and select the folder containing `controls.sii` / `controls_linux.sii`.

List the detected locations without writing anything:

```bash
rayco-ats-controller-manager --diagnose
```

The manager backs up and swaps only known controller-related ATS profile files (`controls.sii`, `controls_linux.sii`, optionally `config_local.cfg`). Each snapshot is tied to the specific ATS profile path. Its own data is in `~/.local/share/rayco-ats-controller-manager/` unless `XDG_DATA_HOME` is set.

### Important: Steam Input layouts are separate

This tool **does not switch Steam Input layouts, control Steam Input on/off, or promise working rumble**. Save Steam Controller / DualSense Steam Input layouts separately in Steam's controller layout editor when needed. For the original Steam Controller, Steam Input is typically essential; ATS's own binding files are only one part of the setup.

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 ats_controller_manager.py --diagnose
bash -n install-fedora.sh update-fedora.sh
```

The Python unit tests use fake ATS profiles in temporary directories and never modify your actual ATS files.
