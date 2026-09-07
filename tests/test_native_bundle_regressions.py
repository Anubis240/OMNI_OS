"""Stdlib regressions for native collection; no native success is simulated."""

import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import release_bundle as bundle


class LinuxCollectionTests(unittest.TestCase):
    def test_selected_qt_file_does_not_scan_unused_qml_sibling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "Qt6/plugins/platforms/libqxcb.so"
            unused = root / "Qt6/qml/QtQuick/Shapes/DesignHelpers/plugin.so"
            for path in (selected, unused):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"\x7fELFfixture")
            self.assertEqual(bundle.linux_elf_inputs([selected]), [selected])
            self.assertEqual(set(bundle.linux_elf_inputs([root])), {selected, unused})

    def test_complete_browser_distribution_still_scanned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browser = root / "firefox"
            browser.write_bytes(b"\x7fELFfixture")
            (root / "resources.txt").write_text("not ELF")
            self.assertEqual(bundle.linux_elf_inputs([root]), [browser])

    def test_qt_ldd_search_path_is_scoped_to_child(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "original"}):
            with patch.object(bundle.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                self.assertEqual(bundle.ldd_dependencies(Path("plugin.so"), [Path("Qt6/lib")]), [])
            self.assertEqual(run.call_args.kwargs["env"]["LD_LIBRARY_PATH"], str(Path("Qt6/lib")))
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "original")

    def test_missing_required_dependency_still_fails(self):
        result = subprocess.CompletedProcess([], 0, "libQt6Multimedia.so.6 => not found\n", "")
        with patch.object(bundle.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "libQt6Multimedia"):
                bundle.ldd_dependencies(Path("plugin.so"), [Path("Qt6/lib")])

    def test_ldd_error_is_not_swallowed(self):
        result = subprocess.CompletedProcess([], 1, "", "ldd failed")
        with patch.object(bundle.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "ldd failed"):
                bundle.ldd_dependencies(Path("plugin.so"))

    def test_selected_elf_transitive_dependencies_are_not_dropped(self):
        selected = Path("qt/libQt6Multimedia.so.6")
        dependency = Path("qt/libQt6Core.so.6")
        transitive = Path("system/libstdc++.so.6")
        graph = {selected: [dependency], dependency: [transitive]}
        paths = [Path("qt")]
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "is_dir", return_value=False):
            with patch.object(bundle, "elf", return_value=True):
                with patch.object(bundle, "ldd_dependencies", side_effect=lambda p, search: graph.get(p, [])) as ldd:
                    result = bundle.linux_binaries([selected], paths, library_scope=Path("qt"))
        self.assertIn((str(dependency), "."), result)
        self.assertIn((str(transitive), "."), result)
        self.assertIn((str(Path("/usr/lib/x86_64-linux-gnu/libportaudio.so.2")), "."), result)
        self.assertEqual(next(call.args[1] for call in ldd.call_args_list if call.args[0] == selected), paths)
        self.assertEqual(next(call.args[1] for call in ldd.call_args_list if call.args[0] == transitive), paths)
        self.assertEqual(next(call.args[1] for call in ldd.call_args_list
                              if call.args[0].name == "libportaudio.so.2"), [])

    def test_browser_context_uses_ancestors_not_qt_or_siblings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browsers = root / "browsers"
            firefox = browsers / "firefox-1/firefox"
            plugin = firefox / "gmp-clearkey/0.1/libclearkey.so"
            plugin.parent.mkdir(parents=True)
            search = bundle.linux_loader_paths(plugin, browsers, [root / "Qt6/lib"], root)
            self.assertEqual(search, [p.resolve() for p in
                                      (plugin.parent, plugin.parent.parent, firefox, firefox.parent)])
            gtk = browsers / "webkit-1/minibrowser-gtk/bin/MiniBrowser"
            gtk.parent.mkdir(parents=True)
            wpe = browsers / "webkit-1/minibrowser-wpe/lib"
            wpe.mkdir(parents=True)
            self.assertNotIn(wpe.resolve(), bundle.linux_loader_paths(gtk, browsers))
            gtk_root = gtk.parent.parent.resolve()
            self.assertEqual(bundle.linux_loader_paths(gtk, browsers),
                             [gtk_root / "lib", gtk_root / "sys/lib"])
            self.assertEqual(bundle.linux_loader_paths(gtk_root / "sys/lib/libjxl.so.0.8", browsers),
                             [gtk_root / "lib", gtk_root / "sys/lib"])

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("cc"), "requires Linux C compiler")
    def test_real_elf_private_sonames_resolve_in_each_load_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            browsers = root / "browsers"
            libraries = []
            plugins = []
            for engine, value in (("firefox-1", 1), ("chromium-1", 2)):
                directory = browsers / engine / "browser"
                plugin = directory / "plugins/nested/plugin.so"
                plugin.parent.mkdir(parents=True)
                source = directory / "private.c"
                source.write_text(f"int private_value(void) {{ return {value}; }}")
                library = directory / "libomni_scope.so"
                subprocess.run(["cc", "-shared", "-fPIC", "-Wl,-soname,libomni_scope.so",
                                str(source), "-o", str(library)], check=True, capture_output=True)
                source.write_text("extern int private_value(void); int plugin(void) { return private_value(); }")
                subprocess.run(["cc", "-shared", "-fPIC", str(source), "-L" + str(directory),
                                "-lomni_scope", "-o", str(plugin)], check=True, capture_output=True)
                libraries.append(library)
                plugins.append(plugin)
            for plugin, library in zip(plugins, libraries):
                with patch.dict(os.environ, {"LD_LIBRARY_PATH": str(libraries[1].parent)}):
                    dependencies = bundle.ldd_dependencies(plugin, bundle.linux_loader_paths(plugin, browsers))
                self.assertIn(library, dependencies)
                self.assertNotIn(libraries[1] if library == libraries[0] else libraries[0], dependencies)
                with self.assertRaisesRegex(RuntimeError, "libomni_scope.so"):
                    bundle.ldd_dependencies(plugin)

    def test_unscoped_ldd_does_not_inherit_private_paths(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/other-engine"}):
            with patch.object(bundle.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                bundle.ldd_dependencies(Path("system.so"))
            self.assertNotIn("LD_LIBRARY_PATH", run.call_args.kwargs["env"])

    def test_private_sonames_stay_relative_and_baseline_is_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browsers = root / "browsers"
            sources = [browsers / engine / "libnss3.so" for engine in ("firefox-1", "chromium-1")]
            for index, source in enumerate(sources):
                source.parent.mkdir(parents=True)
                source.write_bytes(b"\x7fELF" + bytes([index]))
            graph = {source: [source, Path("/lib/libc.so.6")] for source in sources}
            with patch.object(bundle, "linux_elf_inputs", return_value=sources), \
                    patch.object(Path, "is_file", return_value=True), \
                    patch.object(Path, "is_dir", return_value=False), \
                    patch.object(bundle, "ldd_dependencies", side_effect=lambda p, search: graph.get(p, [])):
                result = bundle.linux_binaries([], browser_root=browsers)
            for source in sources:
                destination = str(Path("playwright/driver/package/.local-browsers") / source.parent.name)
                self.assertIn((str(source), destination), result)
                self.assertNotIn((str(source), "."), result)
            self.assertFalse(any(Path(source).name == "libc.so.6" for source, _ in result))

    def test_different_global_soname_bytes_still_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = [root / name / "libconflict.so" for name in ("a", "b")]
            for index, source in enumerate(sources):
                source.parent.mkdir(parents=True)
                source.write_bytes(bytes([index]))
            with patch.object(bundle, "linux_elf_inputs", return_value=sources), \
                    patch.object(Path, "is_file", return_value=True), \
                    patch.object(Path, "is_dir", return_value=False), \
                    patch.object(bundle, "ldd_dependencies", side_effect=lambda p, search: [p] if p in sources else []):
                with self.assertRaisesRegex(RuntimeError, "Conflicting SONAME"):
                    bundle.linux_binaries([])


class LinuxBrowserAssemblyTests(unittest.TestCase):
    def test_complete_tree_keeps_dlopen_libraries_and_rejects_existing_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "browsers"
            for engine in ("chromium", "firefox", "webkit"):
                directory = source / f"{engine}-1" / engine
                directory.mkdir(parents=True)
                (directory / "executable").write_bytes(b"upstream executable")
            library = source / "firefox-1/firefox/libxul.so"
            library.write_bytes(b"ELF loaded only through dlopen")
            library.chmod(0o755)
            output = root / "dist/Omni-OS"
            destination = output / "playwright/driver/package/.local-browsers"
            destination.parent.mkdir(parents=True)
            with patch.object(bundle.sys, "platform", "linux"):
                bundle.append_linux_browsers(output, source)
                self.assertEqual(bundle.browser_tree_manifest(source), bundle.browser_tree_manifest(destination))
                self.assertEqual((destination / library.relative_to(source)).read_bytes(), library.read_bytes())
                self.assertFalse((output / "libxul.so").exists())
                with self.assertRaisesRegex(ValueError, "excluded from COLLECT"):
                    bundle.append_linux_browsers(output, source)

    def test_toc_removes_private_to_global_symlinks_and_global_to_private_aliases(self):
        root = Path("browsers").resolve()
        prefix = "playwright/driver/package/.local-browsers"
        entries = [
            (prefix + "/firefox-1/firefox/libxul.so", "../../../../../../libxul.so", "SYMLINK"),
            ("libxul.so", str(root / "firefox-1/firefox/libxul.so"), "BINARY"),
            ("libnss3.so", prefix + "/firefox-1/firefox/libnss3.so", "SYMLINK"),
            (prefix + "/firefox-1/firefox/omni.ja", str(root / "firefox-1/firefox/omni.ja"), "DATA"),
            ("libsystem.so", "/system/libsystem.so", "BINARY"),
        ]
        self.assertEqual(bundle.without_browser_toc(entries, root), [entries[-1]])

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux symlinks")
    def test_upstream_relative_symlink_and_executable_modes_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "browsers"
            for engine in ("chromium", "firefox", "webkit"):
                (source / f"{engine}-1").mkdir(parents=True)
            library = source / "firefox-1/libxul.so.1"
            library.write_bytes(b"upstream ELF")
            library.chmod(0o755)
            (library.parent / "libxul.so").symlink_to(library.name)
            output = root / "dist"
            destination = output / "playwright/driver/package/.local-browsers"
            destination.parent.mkdir(parents=True)
            bundle.append_linux_browsers(output, source)
            copied = destination / "firefox-1/libxul.so"
            self.assertTrue(copied.is_symlink())
            self.assertEqual(os.readlink(copied), library.name)
            self.assertEqual(copied.stat().st_mode & 0o777, 0o755)


class MacBrowserCollectionTests(unittest.TestCase):
    def test_toc_removes_browser_data_binaries_and_aliases_only(self):
        root = Path("installed/.local-browsers").resolve()
        prefix = "playwright/driver/package/.local-browsers"
        keep = [("playwright/driver/node", "installed/node", "BINARY"),
                ("claude_agent_sdk/_bundled/claude", "installed/claude", "BINARY"),
                (prefix + "-other/file", "installed/unrelated", "DATA")]
        remove = [(prefix + "/firefox/liblgpllibs.dylib", str(root / "firefox/liblgpllibs.dylib"), "BINARY"),
                  (prefix + "/firefox/resource", str(root / "firefox/resource"), "DATA"),
                  ("liblgpllibs.dylib", str(root / "firefox/liblgpllibs.dylib"), "BINARY"),
                  (prefix + "/alias", "relative-target", "SYMLINK"),
                  ("liblgpllibs.dylib", prefix + "/firefox/liblgpllibs.dylib", "SYMLINK"),
                  ("nested/libfreebl3.dylib", "../" + prefix + "/firefox/libfreebl3.dylib", "SYMLINK")]
        self.assertEqual(bundle.without_browser_toc(keep + remove, root), keep)

    def test_manifest_detects_byte_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "liblgpllibs.dylib"
            library.write_bytes(b"original Mach-O bytes")
            original = bundle.browser_tree_manifest(root)
            library.write_bytes(b"modified Mach-O bytes")
            self.assertNotEqual(bundle.browser_tree_manifest(root), original)

    def test_assembly_requires_native_macos(self):
        with patch.object(bundle.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "native macOS"):
                bundle.append_macos_browsers(Path("Omni-OS.app"), Path("browsers"))

    def test_missing_engine_fails_before_signing(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(bundle.sys, "platform", "darwin"), patch.object(bundle.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "Missing bundled chromium"):
                    bundle.append_macos_browsers(Path(temporary) / "Omni-OS.app", Path(temporary))
                run.assert_not_called()

    def test_copy_and_wrapper_only_signing_preserve_browser_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "browsers"
            for engine in ("chromium", "firefox", "webkit"):
                directory = source / f"{engine}-123"
                directory.mkdir(parents=True)
                (directory / "binary").write_bytes(b"unchanged signed bytes")
            app = root / "Omni-OS.app"
            relative = Path("playwright/driver/package/.local-browsers")
            destination = app / "Contents/Resources" / relative
            alias = app / "Contents/Frameworks" / relative
            destination.parent.mkdir(parents=True)
            alias.parent.mkdir(parents=True)
            # Windows may not grant symlink creation privileges. Assert the
            # exact alias request rather than skipping the cross-platform test.
            with patch.object(bundle.sys, "platform", "darwin"), patch.object(Path, "symlink_to") as link:
                with patch.object(bundle.subprocess, "run") as run:
                    bundle.append_macos_browsers(app, source)
            link.assert_called_once_with(os.path.relpath(destination, alias.parent), target_is_directory=True)
            self.assertEqual(bundle.browser_tree_manifest(destination), bundle.browser_tree_manifest(source))
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ["/usr/bin/codesign", "--remove-signature", str(app.resolve())],
                ["/usr/bin/codesign", "--sign", "-", str(app.resolve())],
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app.resolve())],
            ])
            self.assertTrue(all(call.kwargs == {"check": True} for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
