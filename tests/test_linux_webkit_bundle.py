"""Regression coverage for WebKit's isolated lib:sys/lib loader environment."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import release_bundle as bundle


class WebKitDependencyTests(unittest.TestCase):
    def test_global_plugin_dependencies_are_also_available_in_webkit_scope(self):
        browsers = Path("browsers").resolve()
        executable = browsers / "webkit-2336/minibrowser-wpe/bin/MiniBrowser"
        plugins = Path("/usr/lib/x86_64-linux-gnu/gio/modules")
        plugin = plugins / "libgiolibproxy.so"
        dependency = Path("/usr/lib/x86_64-linux-gnu/libproxy.so.1")
        with patch.object(bundle, "linux_elf_inputs", return_value=[executable]), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "is_dir", lambda path: path == plugins), \
                patch.object(Path, "rglob", return_value=[plugin]), \
                patch.object(bundle, "elf", return_value=True), \
                patch.object(bundle, "ldd_dependencies", side_effect=lambda path, search: [dependency] if path == plugin else []):
            result = bundle.linux_binaries([], browser_root=browsers)
        self.assertIn((str(plugin), str(Path("gio/modules"))), result)
        self.assertIn((str(dependency), "."), result)
        self.assertIn((str(dependency), bundle.linux_runtime_destination(executable, browsers)), result)
        self.assertNotIn((str(plugin), "."), result)

    def test_external_closure_keeps_each_runtime_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browsers = root / "browsers"
            wpe = browsers / "webkit-2336/minibrowser-wpe/bin/MiniBrowser"
            gtk = browsers / "webkit-2336/minibrowser-gtk/bin/MiniBrowser"
            firefox = browsers / "firefox-1/firefox/firefox"
            atk = root / "system/libatk-1.0.so.0"
            transitive = root / "system/libglib-2.0.so.0"
            gles = Path("/usr/lib/x86_64-linux-gnu/libGLESv2.so.2")
            gl_dependency = root / "system/libgles-helper.so"

            def dependencies(path, search):
                if path in (wpe, gtk, firefox):
                    return [atk]
                if path == atk:
                    return [transitive]
                if path == gles:
                    return [gl_dependency]
                return []

            with patch.object(bundle, "linux_elf_inputs", return_value=[wpe, gtk, firefox]), \
                    patch.object(bundle, "ldd_dependencies", side_effect=dependencies), \
                    patch.object(Path, "is_file", return_value=True), \
                    patch.object(Path, "is_dir", return_value=False):
                result = bundle.linux_binaries([], browser_root=browsers)
            for library in (atk, transitive, gles, gl_dependency):
                for executable in (wpe, gtk, firefox):
                    self.assertIn((str(library), bundle.linux_runtime_destination(executable, browsers)), result)
            self.assertEqual(bundle.linux_runtime_destination(firefox, browsers), ".")
            self.assertNotEqual(bundle.linux_runtime_destination(wpe, browsers), ".")

    def test_assembly_adds_external_libraries_without_replacing_private_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browsers = root / "browsers"
            for engine in ("chromium-1", "firefox-1", "webkit-2336"):
                (browsers / engine).mkdir(parents=True)
            private = browsers / "webkit-2336/minibrowser-wpe/lib/libprivate.so"
            private.parent.mkdir(parents=True)
            private.write_bytes(b"upstream")
            external = root / "libatk-1.0.so.0"
            external.write_bytes(b"external")
            output = root / "dist"
            prefix = Path("playwright/driver/package/.local-browsers")
            (output / prefix.parent).mkdir(parents=True)
            destination = prefix / "webkit-2336/minibrowser-wpe/sys/lib"
            with patch.object(bundle.sys, "platform", "linux"):
                bundle.append_linux_browsers(output, browsers, [(str(external), str(destination))])
            self.assertEqual((output / destination / external.name).read_bytes(), b"external")
            self.assertEqual((output / prefix / private.relative_to(browsers)).read_bytes(), b"upstream")
            self.assertFalse((output / external.name).exists())
            self.assertEqual(list((output / prefix / "firefox-1").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
