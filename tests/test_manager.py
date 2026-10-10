"""Non-GUI safety tests: python3 -m unittest discover -s tests -v"""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ats_manager', ROOT / 'ats_controller_manager.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.data = Path(self.temp.name) / 'data'
        self.patches = [
            patch.dict(os.environ, {'HOME': str(self.home)}, clear=False),
            patch.object(mod, 'APP_DATA', self.data),
            patch.object(mod, 'SETTINGS_FILE', self.data / 'settings.json'),
            patch.object(mod, 'games_running', lambda: False),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def make_profile(self, base):
        folder = base / 'steam_profiles' / '54657374447269766572'
        folder.mkdir(parents=True)
        (folder / 'controls.sii').write_text('controller-v1\n')
        (folder / 'config_local.cfg').write_text('device settings\n')
        return folder

    def test_proton_priority_and_secondary_library(self):
        native = self.make_profile(self.home / '.local/share/American Truck Simulator')
        library = self.home / 'SteamLibrary'
        proton = self.make_profile(library / 'steamapps/compatdata/270880/pfx/drive_c/users/steamuser/Documents/American Truck Simulator')
        steam = self.home / '.local/share/Steam/steamapps'
        steam.mkdir(parents=True)
        (steam / 'libraryfolders.vdf').write_text('"libraryfolders"\n{\n"1" { "path" "' + str(library) + '" }\n}\n')
        actual = mod.discover_profiles()
        self.assertEqual(actual[0], proton)
        self.assertEqual(set(actual), {proton, native})
        self.assertIn('Proton', mod.friendly_name(proton))

    def test_explicit_custom_compatdata_prefix(self):
        custom = Path(self.temp.name) / 'CustomWineLandPrefix'
        folder = self.make_profile(custom / 'pfx/drive_c/users/steamuser/Documents/American Truck Simulator')
        with patch.dict(os.environ, {'STEAM_COMPAT_DATA_PATH': str(custom)}):
            self.assertIn(folder, mod.discover_profiles())

    def test_capture_controls_without_optional_config(self):
        folder = self.make_profile(self.home / 'Fake ATS')
        snap = self.data / 'only_controls'
        manifest = mod.capture(folder, snap, 'DualSense', include_local=False)
        self.assertEqual(manifest['files'], ['controls.sii'])
        self.assertFalse((snap / 'config_local.cfg').exists())

    def test_updater_and_installer_available(self):
        self.assertTrue((ROOT / 'update-fedora.sh').is_file())
        self.assertTrue((ROOT / 'install-fedora.sh').is_file())

    def test_save_and_restore_without_touching_other_files(self):
        folder = self.make_profile(self.home / '.local/share/Steam/steamapps/compatdata/270880/pfx/drive_c/users/steamuser/Documents/American Truck Simulator')
        (folder / 'game.sii').write_text('SAVE GAME STAYS INTACT')
        snap = self.data / 'test_snapshot'
        mod.capture(folder, snap, 'Steam Controller')
        (folder / 'controls.sii').write_text('new-configuration\n')
        files, backup = mod.apply_files(folder, snap)
        self.assertIn('controls.sii', files)
        self.assertEqual((folder / 'controls.sii').read_text(), 'controller-v1\n')
        self.assertEqual((folder / 'game.sii').read_text(), 'SAVE GAME STAYS INTACT')
        self.assertEqual((backup / 'controls.sii').read_text(), 'new-configuration\n')


if __name__ == '__main__':
    unittest.main()
