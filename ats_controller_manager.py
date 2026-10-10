#!/usr/bin/env python3
"""Ray Co. ATS Controller Manager - local controller binding snapshots for Fedora.

Prioritizes Steam/Proton ATS (including WineLand and additional Steam libraries)
but also supports the native Linux version.
Never touches game saves, Steam Input layouts, or files outside ATS local profile
input configuration and its own application directory.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import queue
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import sys
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import uuid

APP_TITLE = "Ray Co. | ATS Controller Manager"
VERSION = "0.3.0"
APP_ID = "270880"
CONFIG_FILES = ("controls.sii", "controls_linux.sii", "config_local.cfg")
LAYOUT_FILES = ("controls.sii", "controls_linux.sii")
APP_DATA = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "rayco-ats-controller-manager"
SETTINGS_FILE = APP_DATA / "settings.json"
BG, PANEL, FIELD, BORDER = "#141a20", "#202a34", "#131c26", "#364652"
FG, MUTED, ACCENT = "#e8edf2", "#a5b2c0", "#64ced0"


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def profile_id(folder: Path) -> str:
    # A full path hash prevents collisions between native and Proton installs.
    return hashlib.sha256(str(folder.resolve()).encode("utf-8")).hexdigest()[:20]


def friendly_name(folder: Path) -> str:
    label = folder.name
    if re.fullmatch(r"[a-fA-F0-9]{6,}", label) and len(label) % 2 == 0:
        try:
            decoded = bytes.fromhex(label).decode("utf-8")
            if decoded.isprintable() and decoded.strip():
                label = decoded
        except (UnicodeError, ValueError):
            pass
    parent = "Cloud" if folder.parent.name == "steam_profiles" else "Local"
    kind = "Proton" if "compatdata" in folder.parts else "Native"
    return f"{label}  •  {kind} / {parent}"


def steam_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home / ".local/share/Steam",
        home / ".steam/root",
        home / ".steam/steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    for root in list(roots):
        vdf = root / "steamapps/libraryfolders.vdf"
        if not vdf.is_file():
            continue
        try:
            body = vdf.read_text(encoding="utf-8", errors="replace")
            roots.extend(Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s*"([^"\n]+)"', body))
        except OSError:
            pass
    result, seen = [], set()
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key not in seen:
            seen.add(key)
            result.append(root)
    return result


def ats_homes() -> list[Path]:
    """Find ATS user-data folders; WineLand uses ordinary Proton compatdata.

    Do not assume that the executable or compatibility tool location is where
    its profile lives. The per-game prefix is identified by Steam app 270880.
    An overridden STEAM_COMPAT_DATA_PATH is also supported when supplied.
    """
    home = Path.home()
    candidates: list[Path] = []
    prefixes: list[Path] = []
    override = os.environ.get("STEAM_COMPAT_DATA_PATH", "").strip()
    if override:
        prefixes.append(Path(override).expanduser() / "pfx")
    for root in steam_roots():
        prefixes.append(root / "steamapps/compatdata" / APP_ID / "pfx")
    for prefix in prefixes:
        users = prefix / "drive_c/users"
        # steamuser is the normal Proton user, but alternative custom prefixes
        # may have a different Windows username.
        names = [users / "steamuser"]
        if users.is_dir():
            try:
                names.extend(p for p in users.iterdir() if p.is_dir() and p.name != "steamuser")
            except OSError:
                pass
        for user in names:
            for docs in ("Documents", "My Documents"):
                candidates.append(user / docs / "American Truck Simulator")
    # Native Linux is a fallback, not the preferred installation for this user.
    candidates.extend([
        home / ".local/share/American Truck Simulator",
        home / ".var/app/com.valvesoftware.Steam/data/American Truck Simulator",
    ])
    found, seen = [], set()
    for folder in candidates:
        try:
            key = str(folder.resolve())
        except OSError:
            key = str(folder)
        if folder.is_dir() and key not in seen:
            seen.add(key)
            found.append(folder)
    return found


def discover_profiles(extra: list[str] | None = None) -> list[Path]:
    candidates = []
    for folder in ats_homes():
        for parent in ("steam_profiles", "profiles"):
            path = folder / parent
            if path.is_dir():
                try:
                    candidates.extend(p for p in path.iterdir() if p.is_dir())
                except OSError:
                    pass
    for p in extra or []:
        folder = Path(p).expanduser()
        if folder.is_dir():
            candidates.append(folder)
    results, seen = [], set()
    for p in candidates:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved in seen or not any((resolved / f).is_file() for f in CONFIG_FILES):
            continue
        seen.add(resolved)
        results.append(resolved)
    # Prefer Proton/WineLand when both Proton and Linux-native profiles exist.
    return sorted(
        results,
        key=lambda p: (
            "compatdata" in p.parts,
            max((p / f).stat().st_mtime for f in CONFIG_FILES if (p / f).is_file()),
        ),
        reverse=True,
    )


def games_running() -> bool:
    """Avoid conflicting with ATS overwriting controls when it closes."""
    try:
        result = subprocess.run(["ps", "-eo", "comm=,args="], capture_output=True,
                                text=True, timeout=4, check=False)
        for line in result.stdout.splitlines():
            if re.search(r"\bamtrucks(?:\.exe)?\b", line, re.I):
                return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


def snapshot_store(folder: Path) -> Path:
    return APP_DATA / "profiles" / profile_id(folder)


def backup_store(folder: Path) -> Path:
    return APP_DATA / "backups" / profile_id(folder)


def existing_files(folder: Path, include_local: bool = True) -> list[str]:
    names = CONFIG_FILES if include_local else LAYOUT_FILES
    return [f for f in names if (folder / f).is_file()]


def capture(folder: Path, dest: Path, label: str, include_local: bool = True) -> dict:
    files = existing_files(folder, include_local)
    if not any(f in files for f in LAYOUT_FILES):
        raise ValueError("No controls.sii or controls_linux.sii found in the chosen ATS profile.")
    dest.mkdir(parents=True, exist_ok=False)
    try:
        for file in files:
            shutil.copy2(folder / file, dest / file)
        metadata = {"version": 1, "name": label, "source": str(folder),
                    "created_at": now(), "files": files}
        (dest / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return metadata
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def saved_layouts(folder: Path) -> list[tuple[Path, dict]]:
    root = snapshot_store(folder)
    out = []
    if root.is_dir():
        for p in root.iterdir():
            if not p.is_dir() or not (p / "manifest.json").is_file():
                continue
            try:
                meta = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
                if meta.get("version") == 1 and all(f in CONFIG_FILES for f in meta.get("files", [])):
                    out.append((p, meta))
            except (OSError, ValueError, TypeError):
                pass
    return sorted(out, key=lambda e: e[1].get("created_at", ""), reverse=True)


def backup_now(folder: Path) -> Path:
    dest = backup_store(folder) / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    files = existing_files(folder)
    dest.mkdir(parents=True)
    for file in files:
        shutil.copy2(folder / file, dest / file)
    metadata = {"version": 1, "name": "Automatic pre-switch backup", "source": str(folder),
                "created_at": now(), "files": files}
    (dest / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return dest


def latest_backup(folder: Path) -> Path | None:
    base = backup_store(folder)
    if not base.is_dir():
        return None
    folders = sorted((f for f in base.iterdir() if f.is_dir() and (f / "manifest.json").is_file()), reverse=True)
    return folders[0] if folders else None


def apply_files(folder: Path, snapshot: Path) -> tuple[list[str], Path]:
    """Validate before touching user files; backup originals and atomically replace."""
    if games_running():
        raise RuntimeError("ATS is running. Close the game completely before switching layouts.")
    meta = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if meta.get("version") != 1 or meta.get("source") != str(folder):
        raise ValueError("This snapshot belongs to a different ATS profile.")
    files = meta.get("files", [])
    if not isinstance(files, list) or not files or not all(f in CONFIG_FILES for f in files):
        raise ValueError("The snapshot contains an invalid file list.")
    if not any(f in files for f in LAYOUT_FILES):
        raise ValueError("This snapshot is missing the controller binding file.")
    if any(not (snapshot / f).is_file() for f in files):
        raise ValueError("A saved layout file is missing. Nothing was changed.")
    if not folder.is_dir():
        raise FileNotFoundError("The ATS profile folder no longer exists.")
    backup = backup_now(folder)
    # Staged files minimize the risk of partial or corrupted writes.
    staged = []
    try:
        for file in files:
            fd, tmpname = tempfile.mkstemp(prefix=f".{file}.", suffix=".tmp", dir=folder)
            os.close(fd)
            p = Path(tmpname)
            staged.append((p, folder / file))
            shutil.copy2(snapshot / file, p)
        for temp, dest in staged:
            os.replace(temp, dest)
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)
    return files, backup


def open_folder(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["xdg-open", str(folder)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_settings() -> dict:
    try:
        d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_settings(settings: dict) -> None:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


class Manager(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("790x670")
        self.minsize(700, 600)
        self.configure(bg=BG)
        self.settings = read_settings()
        self.update_results: queue.Queue = queue.Queue()
        self.update_running = False
        self.profiles: list[Path] = []
        self.layouts: list[tuple[Path, dict]] = []
        self.active: Path | None = None
        self.dark = tk.BooleanVar(value=self.settings.get("dark", True))
        self.include_local = tk.BooleanVar(value=self.settings.get("include_local", False))
        self.profile_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Finding ATS profiles...")
        self._style()
        self._ui()
        self.refresh_profiles()
        self.after(4000, self.poll_game)

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TCombobox", fieldbackground=FIELD, background=PANEL, foreground=FG,
                    selectbackground=FIELD, selectforeground=FG, bordercolor=BORDER, padding=7)
        s.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)])
        s.configure("Treeview", background=FIELD, foreground=FG, fieldbackground=FIELD,
                    borderwidth=0, rowheight=37, font=("DejaVu Sans", 10))
        s.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                    font=("DejaVu Sans", 10, "bold"), padding=7)
        s.map("Treeview", background=[("selected", "#235768")], foreground=[("selected", "#ffffff")])
        s.configure("TCheckbutton", background=BG, foreground=MUTED, font=("DejaVu Sans", 10))
        s.map("TCheckbutton", background=[("active", BG)])

    def _button(self, container, text, command, accent=False):
        bg = "#247f8c" if accent else PANEL
        b = tk.Button(container, text=text, command=command, relief="flat", bg=bg, fg=FG,
                      activebackground="#3198a4" if accent else "#314150", activeforeground="white",
                      cursor="hand2", font=("DejaVu Sans", 10, "bold"), padx=14, pady=12,
                      highlightthickness=1, highlightbackground=BORDER)
        return b

    def _ui(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 8))
        tk.Label(header, text="ATS CONTROLLER MANAGER", bg=BG, fg=FG,
                 font=("DejaVu Sans", 18, "bold")).pack(anchor="w")
        tk.Label(header, text=f"RAY CO.  /  FEDORA  /  PROTON & WINELAND  /  v{VERSION}", bg=BG, fg=ACCENT,
                 font=("DejaVu Sans", 9, "bold")).pack(anchor="w", pady=(3, 0))

        selector = tk.Frame(self, bg=BG)
        selector.pack(fill="x", padx=24, pady=(9, 0))
        tk.Label(selector, text="ATS SAVE PROFILE (PROTON PRIORITY)", bg=BG, fg=MUTED,
                 font=("DejaVu Sans", 10, "bold")).pack(anchor="w", pady=(0, 7))
        line = tk.Frame(selector, bg=BG)
        line.pack(fill="x")
        self.profile_box = ttk.Combobox(line, textvariable=self.profile_var, state="readonly")
        self.profile_box.pack(side="left", fill="x", expand=True)
        self.profile_box.bind("<<ComboboxSelected>>", self.on_profile)
        self._button(line, "Browse...", self.browse).pack(side="right", padx=(9, 0))

        center = tk.Frame(self, bg=BG)
        center.pack(fill="both", expand=True, padx=24, pady=(16, 0))
        tk.Label(center, text="SAVED CONTROLLER LAYOUTS", bg=BG, fg=MUTED,
                 font=("DejaVu Sans", 10, "bold")).pack(anchor="w", pady=(0, 6))
        self.tree = ttk.Treeview(center, columns=("name", "files", "date"), show="headings", height=6)
        self.tree.heading("name", text="Layout")
        self.tree.heading("files", text="Saved files")
        self.tree.heading("date", text="Captured")
        self.tree.column("name", width=200, stretch=True)
        self.tree.column("files", width=245, stretch=True)
        self.tree.column("date", width=150, stretch=False)
        self.tree.pack(fill="both", expand=True)

        opts = tk.Frame(self, bg=BG)
        opts.pack(fill="x", padx=24, pady=(11, 4))
        ttk.Checkbutton(opts, text="Include config_local.cfg (device/game settings)",
                        variable=self.include_local, command=self.settings_changed).pack(side="left")

        actions = tk.Frame(self, bg=BG)
        actions.pack(fill="x", padx=24, pady=(9, 8))
        for col in range(3):
            actions.grid_columnconfigure(col, weight=1, uniform="buttons")
        buttons = [
            ("Save Current Layout", self.save_layout, True),
            ("Apply Selected Layout", self.apply_layout, True),
            ("Undo Last Switch", self.undo_switch, False),
            ("Open ATS Profile", self.open_profile, False),
            ("Open Saved Layouts", self.open_saved, False),
            ("Refresh / Detect", self.refresh_profiles, False),
        ]
        for i, (label, cmd, color) in enumerate(buttons):
            self._button(actions, label, cmd, color).grid(row=i // 3, column=i % 3,
                                                          sticky="ew", padx=4, pady=4)
        self.update_button = self._button(actions, "Check / Update From GitHub", self.update_from_github)
        self.update_button.grid(row=2, column=0, columnspan=3, sticky="ew", padx=4, pady=4)

        footer = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(fill="x", side="bottom", padx=24, pady=(8, 17))
        tk.Label(footer, textvariable=self.status_var, bg=PANEL, fg=FG, anchor="w", justify="left",
                 wraplength=690, font=("DejaVu Sans", 9)).pack(fill="x", padx=12, pady=10)

    def settings_changed(self):
        self.settings["include_local"] = self.include_local.get()
        write_settings(self.settings)

    def status(self, message):
        self.status_var.set(message)

    def refresh_profiles(self):
        selected = str(self.active) if self.active else self.settings.get("selected_profile", "")
        self.profiles = discover_profiles(self.settings.get("extra_paths", []))
        if not self.profiles:
            self.active = None
            self.profile_box["values"] = []
            self.profile_var.set("")
            self.load_layouts()
            self.status("No ATS profiles detected. Open ATS once, or use Browse to select its profile folder.")
            return
        options = [f"{friendly_name(p)}    [{str(p.parent.parent)}]" for p in self.profiles]
        self.profile_box["values"] = options
        idx = next((i for i, p in enumerate(self.profiles) if str(p) == selected), 0)
        self.profile_box.current(idx)
        self.choose_profile(idx)

    def on_profile(self, _event=None):
        idx = self.profile_box.current()
        if idx >= 0:
            self.choose_profile(idx)

    def choose_profile(self, idx):
        self.active = self.profiles[idx]
        self.settings["selected_profile"] = str(self.active)
        write_settings(self.settings)
        self.load_layouts()
        names = ", ".join(existing_files(self.active)) or "none"
        self.status(f"Selected: {self.active}\nFiles: {names}")

    def browse(self):
        folder = filedialog.askdirectory(title="Choose the folder containing ATS controls.sii / controls_linux.sii")
        if not folder:
            return
        folder = str(Path(folder).resolve())
        if not existing_files(Path(folder)):
            messagebox.showerror("Invalid profile", "Choose an ATS profile folder containing controls.sii or controls_linux.sii.")
            return
        extras = self.settings.setdefault("extra_paths", [])
        if folder not in extras:
            extras.append(folder)
        self.settings["selected_profile"] = folder
        write_settings(self.settings)
        self.refresh_profiles()

    def load_layouts(self):
        self.tree.delete(*self.tree.get_children())
        self.layouts = saved_layouts(self.active) if self.active else []
        for index, (_, meta) in enumerate(self.layouts):
            self.tree.insert("", "end", iid=str(index), values=(
                meta.get("name", "Unnamed"), ", ".join(meta.get("files", [])),
                meta.get("created_at", "").replace("T", " ")[:16]))

    def chosen_layout(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Choose a layout", "Select a saved controller layout first.")
            return None
        return self.layouts[int(selected[0])]

    def ensure_closed(self):
        if games_running():
            messagebox.showwarning("Close ATS first", "Quit ATS completely before saving, applying, or restoring bindings.\n\nATS writes its controller settings when it exits.")
            return False
        return True

    def save_layout(self):
        if not self.active or not self.ensure_closed():
            return
        name = simpledialog.askstring("Save controller layout", "Name this layout (e.g. DualSense, Steam Controller, Xbox):", parent=self)
        if name is None:
            return
        name = name.strip()
        if not name or len(name) > 60:
            messagebox.showerror("Invalid name", "Enter a name between 1 and 60 characters.")
            return
        if any(meta["name"].casefold() == name.casefold() for _, meta in self.layouts):
            messagebox.showwarning("Name already used", "That layout already exists. Pick a new name, such as 'DualSense v2'.")
            return
        destination = snapshot_store(self.active) / uuid.uuid4().hex
        try:
            meta = capture(self.active, destination, name, self.include_local.get())
            self.load_layouts()
            self.status(f"Saved '{name}' ({', '.join(meta['files'])}). Original ATS files untouched.")
        except Exception as e:
            messagebox.showerror("Unable to save", str(e))

    def apply_layout(self):
        if not self.active or not self.ensure_closed():
            return
        choice = self.chosen_layout()
        if not choice:
            return
        directory, meta = choice
        if not messagebox.askyesno("Apply controller layout", f"Apply '{meta['name']}' to:\n\n{friendly_name(self.active)}?\n\nYour current controls will be backed up first. ATS must stay closed."):
            return
        try:
            files, backup = apply_files(self.active, directory)
            self.status(f"Applied '{meta['name']}' ({', '.join(files)}). Previous layout backed up: {backup.name}")
            messagebox.showinfo("Layout activated", f"'{meta['name']}' is ready. You can now launch ATS.")
        except Exception as e:
            messagebox.showerror("Unable to apply", str(e))

    def undo_switch(self):
        if not self.active or not self.ensure_closed():
            return
        backup = latest_backup(self.active)
        if not backup:
            messagebox.showinfo("No backups", "There isn't an automatic backup yet. One is created before each layout switch.")
            return
        if not messagebox.askyesno("Restore previous configuration", f"Restore the last pre-switch backup?\n\n{backup.name}\n\nA safety backup of your current layout will also be made."):
            return
        try:
            files, saved = apply_files(self.active, backup)
            self.status(f"Restored backup {backup.name} ({', '.join(files)}). Safety copy: {saved.name}")
        except Exception as e:
            messagebox.showerror("Unable to restore", str(e))

    def open_profile(self):
        if self.active:
            try:
                open_folder(self.active)
            except OSError as e:
                messagebox.showerror("Unable to open", str(e))

    def open_saved(self):
        if self.active:
            try:
                open_folder(snapshot_store(self.active))
            except OSError as e:
                messagebox.showerror("Unable to open", str(e))

    def update_from_github(self):
        """Update only the app code, never game config or saved layouts."""
        if self.update_running:
            return
        if not messagebox.askyesno(
            "Update Controller Manager",
            "Check Firefoxray/ATS-Controller-Manager for updates?\n\n"
            "This only updates the installed application. Your saved layouts, "
            "ATS profiles and backups remain untouched.\n\n"
            "An internet connection and Git are required.", parent=self):
            return
        updater = Path(__file__).resolve().parent / "update-fedora.sh"
        if not updater.is_file():
            messagebox.showerror("Updater not installed", "Missing update-fedora.sh. "
                                 "Clone the GitHub repository and rerun ./install-fedora.sh.")
            return
        self.update_running = True
        self.update_button.configure(state="disabled", text="Checking GitHub...")
        self.status("Checking the GitHub repository for a newer Controller Manager...")
        threading.Thread(target=self._run_update, args=(updater,), daemon=True).start()
        self.after(250, self._poll_update)

    def _run_update(self, updater: Path):
        try:
            result = subprocess.run(["bash", str(updater)], capture_output=True, text=True,
                                    timeout=180, check=False)
            output = (result.stdout + "\n" + result.stderr).strip()
            APP_DATA.mkdir(parents=True, exist_ok=True)
            (APP_DATA / "update.log").write_text(output + "\n", encoding="utf-8")
            self.update_results.put((result.returncode, output))
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.update_results.put((1, str(exc)))

    def _poll_update(self):
        try:
            code, output = self.update_results.get_nowait()
        except queue.Empty:
            self.after(250, self._poll_update)
            return
        self.update_running = False
        self.update_button.configure(state="normal", text="Check / Update From GitHub")
        if code:
            self.status("Update failed. Your current installation and ATS settings are unchanged.")
            messagebox.showerror("Update failed", (output[-2500:] or "Unknown updater error") +
                                 "\n\nSee ~/.local/share/rayco-ats-controller-manager/update.log")
            return
        self.status(output.splitlines()[-1] if output else "Update completed.")
        if messagebox.askyesno("Update finished", "Update completed successfully. "
                               "Restart Controller Manager now to load the installed version?", parent=self):
            installed = APP_DATA / "app" / "ats_controller_manager.py"
            os.execv(sys.executable, [sys.executable, str(installed)])

    def poll_game(self):
        if games_running():
            self.status("ATS appears to be running. Finish your drive and quit ATS before switching layouts.")
        self.after(6000, self.poll_game)


def diagnose() -> None:
    """Non-destructive diagnostic for users troubleshooting Proton path detection."""
    print(f"Ray Co. ATS Controller Manager v{VERSION}")
    print("Steam roots:")
    for root in steam_roots():
        if root.is_dir():
            print(f"  {root}")
    print("ATS data folders:")
    folders = ats_homes()
    for folder in folders:
        print(f"  {folder}")
    if not folders:
        print("  None detected (Steam/ATS may not have created its profile yet)")
    print("Controller profiles, Proton first:")
    profiles = discover_profiles(read_settings().get("extra_paths", []))
    for profile in profiles:
        print(f"  {profile} [{', '.join(existing_files(profile))}]")
    if not profiles:
        print("  None found; choose the folder containing controls.sii via Browse")


if __name__ == "__main__":
    if "--diagnose" in sys.argv:
        diagnose()
        sys.exit(0)
    try:
        Manager().mainloop()
    except tk.TclError as exc:
        print(f"Unable to open desktop UI: {exc}", file=sys.stderr)
        sys.exit(1)
