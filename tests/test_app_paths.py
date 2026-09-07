"""Path selection is isolated from the host OS and never writes user data."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import app_paths


class AppPathsTests(unittest.TestCase):
    def test_source_retains_repository_root(self):
        expected = Path(app_paths.__file__).resolve().parent.parent
        with patch.object(app_paths.sys, "frozen", False, create=True):
            for system in ("win32", "darwin", "linux"):
                with self.subTest(system=system), patch.object(app_paths.sys, "platform", system):
                    self.assertEqual(app_paths.get_data_dir(), expected)
                    self.assertEqual(app_paths.get_resource_dir(), expected)

    def test_frozen_resources_use_meipass(self):
        with patch.object(app_paths.sys, "frozen", True, create=True), \
             patch.object(app_paths.sys, "_MEIPASS", "bundle-resources", create=True):
            for system in ("win32", "darwin", "linux"):
                with self.subTest(system=system), patch.object(app_paths.sys, "platform", system):
                    self.assertEqual(app_paths.get_resource_dir(), Path("bundle-resources"))

    def test_frozen_resource_fallback(self):
        runtime = SimpleNamespace(frozen=True, executable=str(Path.cwd() / "bundle" / "Omni"))
        with patch.object(app_paths, "sys", runtime):
            self.assertEqual(app_paths.get_resource_dir(), Path(app_paths.sys.executable).resolve().parent)

    def test_windows_keeps_install_data(self):
        executable = Path.cwd() / "installation" / "Omni.exe"
        with patch.object(app_paths.sys, "frozen", True, create=True), \
             patch.object(app_paths.sys, "platform", "win32"), \
             patch.object(app_paths.sys, "executable", str(executable)):
            self.assertEqual(app_paths.get_data_dir(), executable.resolve().parent)

    def test_mac_uses_application_support(self):
        home = Path.cwd() / "fake-home"
        with patch.object(app_paths.sys, "frozen", True, create=True), \
             patch.object(app_paths.sys, "platform", "darwin"), \
             patch.object(app_paths.Path, "home", return_value=home):
            self.assertEqual(app_paths.get_data_dir(), home / "Library" / "Application Support" / "Omni-OS")

    def test_linux_xdg_and_fallback(self):
        home = Path.cwd() / "fake-home"
        absolute_xdg = Path.cwd() / "xdg-data"
        with patch.object(app_paths.sys, "frozen", True, create=True), \
             patch.object(app_paths.sys, "platform", "linux"), \
             patch.object(app_paths.Path, "home", return_value=home):
            for value in ("", "relative-data", str(absolute_xdg)):
                with self.subTest(xdg=value), patch.dict(app_paths.os.environ, {"XDG_DATA_HOME": value}):
                    expected = absolute_xdg if value == str(absolute_xdg) else home / ".local" / "share"
                    self.assertEqual(app_paths.get_data_dir(), expected / "Omni-OS")

    def test_helpers_do_not_create_or_open_files(self):
        with patch.object(app_paths.sys, "frozen", True, create=True), \
             patch.object(app_paths.sys, "platform", "darwin"), \
             patch.object(app_paths.Path, "home", return_value=Path("fake-home")), \
             patch.object(app_paths.Path, "mkdir", side_effect=AssertionError("mkdir")), \
             patch("builtins.open", side_effect=AssertionError("open")):
            app_paths.get_data_dir()
            app_paths.get_resource_dir()
            app_paths.get_os_name()

    def test_native_os_names(self):
        for system, expected in (("win32", "windows"), ("darwin", "mac"), ("linux", "linux")):
            with self.subTest(system=system), patch.object(app_paths.sys, "platform", system):
                self.assertEqual(app_paths.get_os_name(), expected)


if __name__ == "__main__":
    unittest.main()
