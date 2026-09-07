"""Real Python copies with synthetic trees; hdiutil/codesign are always mocked."""

from contextlib import chdir, contextmanager, ExitStack
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import release_bundle as bundle


class MacPackageDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "dist/Omni-OS.app"
        self.root.mkdir(parents=True)
        self.resource = self.root / "resource"
        self.resource.write_bytes(b"original signed resource")
        # Both AppleDouble magic and unrelated bytes MUST survive as literal files.
        (self.root / "._metadata.json").write_bytes(b"\x00\x05\x16\x07" + bytes(64))
        (self.root / "._ordinary").write_bytes(b"not AppleDouble")
        self.output = self.base / "release-assets"
        self.events = []
        self.commands = []
        self.workspace = None

    @contextmanager
    def native_fixture(self, *, target="macos-arm64", failures=(), mutation=None,
                       malformed=False, cleanup_failure=False):
        original_copytree = shutil.copytree
        original_rmtree = shutil.rmtree
        original_mkdtemp = tempfile.mkdtemp
        self.native_error = subprocess.CalledProcessError(
            1, ["native fixture"], output="native stdout", stderr="sealed resource invalid")
        self.detach_error = subprocess.CalledProcessError(
            16, ["detach fixture"], output="detach stdout", stderr="Resource busy")

        def mkdtemp(*args, **kwargs):
            if kwargs.get("prefix") != "omni-final-package-":
                return original_mkdtemp(*args, **kwargs)
            self.workspace = Path(original_mkdtemp(dir=self.base, **kwargs))
            return str(self.workspace)

        def copytree(source, destination, *args, **kwargs):
            if Path(source) == self.root:
                self.events.append("copy")
                self.assertTrue(kwargs["symlinks"])
            result = original_copytree(source, destination, *args, **kwargs)
            if Path(source) == self.root and mutation == "copy":
                (Path(destination) / "._metadata.json").unlink()
            return result

        def rmtree(path, *args, **kwargs):
            if Path(path) == self.workspace and cleanup_failure:
                raise PermissionError("cleanup unavailable")
            return original_rmtree(path, *args, **kwargs)

        def native_run(command, **kwargs):
            self.commands.append(command)
            self.assertTrue(kwargs["check"])
            self.assertGreater(kwargs["timeout"], 0)
            self.assertTrue(kwargs["text"])
            self.assertTrue(kwargs["capture_output"])
            stdout = "native stdout"
            if command[0] == "/usr/bin/codesign":
                self.assertEqual(command[1:-1], ["--verify", "--deep", "--strict", "--verbose=4"])
                path = Path(command[-1])
                stage = ("pre-image-verify" if path == self.root else
                         "copy-image-verify" if path.parent.name == "writable" else "post-image-verify")
            else:
                self.assertEqual(command[0], "/usr/bin/hdiutil")
                verb = command[1]
                if verb == "attach":
                    readonly = "-readonly" in command
                    stage = "attach-readonly" if readonly else "attach-writable"
                    mountpoint = Path(command[command.index("-mountpoint") + 1])
                    self.assertTrue(mountpoint.is_dir())
                    self.assertIn("-plist", command)
                    self.assertIn("-nobrowse", command)
                    if readonly:
                        self.assertEqual(Path(command[2]).suffix, ".dmg")
                        original_copytree(self.workspace / "writable" / self.root.name,
                                          mountpoint / self.root.name, symlinks=True)
                        if mutation == "final":
                            (mountpoint / self.root.name / "._metadata.json").unlink()
                    stdout = ("bad plist" if malformed else plistlib.dumps({"system-entities": [
                        {"dev-entry": "/dev/mock", "mount-point": str(mountpoint)}]}).decode())
                elif verb == "detach":
                    stage = "detach-" + ("readonly" if Path(command[2]).name == "readonly" else "writable")
                elif verb == "create":
                    stage = "create-image"
                    self.assertNotIn("-srcfolder", command)
                    self.assertEqual(command[command.index("-fs") + 1], "APFS")
                    self.assertEqual(command[command.index("-type") + 1], "SPARSE")
                    self.assertEqual(command[command.index("-volname") + 1], "Omni-OS")
                    self.assertFalse(Path(command[2]).is_relative_to(self.output))
                    self.assertGreater(int(command[command.index("-size") + 1][:-1]), 256)
                    Path(command[2]).write_bytes(b"sparse fixture")
                    if mutation == "source":
                        self.resource.write_bytes(b"changed after original snapshot")
                elif verb == "convert":
                    stage = "convert-image"
                    self.assertEqual(self.events[-1], "detach-writable")
                    self.assertEqual(command[command.index("-format") + 1], "UDZO")
                    Path(command[-1]).write_bytes(b"synthetic DMG" + b"koly" + bytes(508))
                else:
                    self.fail(f"Unexpected native command: {command}")
            self.events.append(stage)
            if stage in failures:
                raise self.detach_error if stage.startswith("detach-") else self.native_error
            return subprocess.CompletedProcess(command, 0, stdout, "native stderr")

        def smoke(*args, **kwargs):
            self.events.append("smoke")
            self.assertIn(str(self.workspace / "readonly"), args[0][0])
            self.assertEqual(self.events[-2], "post-image-verify")
            return Mock(**{"wait.return_value": 1 if "smoke" in failures else 0})

        with ExitStack() as stack:
            stack.enter_context(chdir(self.base))
            stack.enter_context(patch.dict(os.environ, {"RUNNER_TEMP": str(self.base),
                                "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123"}))
            stack.enter_context(patch.object(bundle.sys, "platform", "darwin"))
            stack.enter_context(patch.object(bundle.platform, "system", return_value="Darwin"))
            stack.enter_context(patch.object(bundle.platform, "machine",
                                return_value="x86_64" if target == "macos-x64" else "arm64"))
            stack.enter_context(patch.object(bundle.tempfile, "mkdtemp", side_effect=mkdtemp))
            stack.enter_context(patch.object(bundle.shutil, "copytree", side_effect=copytree))
            cleanup = stack.enter_context(patch.object(bundle.shutil, "rmtree", side_effect=rmtree))
            stack.enter_context(patch.object(bundle.subprocess, "run", side_effect=native_run))
            stack.enter_context(patch.object(bundle.subprocess, "Popen", side_effect=smoke))
            stack.enter_context(patch.object(bundle, "verify_report"))
            archives = stack.enter_context(patch.object(bundle, "archive_members_safe"))
            yield cleanup
            archives.assert_not_called()

    def report(self, target="macos-arm64"):
        return json.loads((self.base / f"smoke-package-{target}.json").read_text(encoding="utf-8"))

    def test_complete_dmg_flow_preserves_literal_appledouble_files(self):
        for target in ("macos-arm64", "macos-x64"):
            with self.subTest(target=target), self.native_fixture(target=target):
                bundle.package(target, "1.10.0", self.output / target)
            report = self.report(target)
            self.assertEqual(report["stage"], "complete")
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["retained_paths"], [])
            self.assertEqual(report["manifests"]["before"], report["manifests"]["copied"])
            self.assertEqual(report["manifests"]["before"], report["manifests"]["after"])
            self.assertEqual(report["diff"]["counts"], dict(added=0, removed=0, changed=0))
            self.assertIn("xattrs", report["limitations"])
            self.assertFalse(self.workspace.exists())
            manifest = json.loads((self.output / target / "manifest.json").read_text())
            self.assertEqual(set(manifest["assets"]), set(bundle.asset_names(target, "1.10.0")))
            self.assertEqual(self.events, ["pre-image-verify", "create-image", "attach-writable", "copy",
                "copy-image-verify", "detach-writable", "convert-image", "attach-readonly",
                "post-image-verify", "smoke", "detach-readonly"])
            self.events.clear()

    def test_differences_block_manifest_and_smoke(self):
        for mutation in ("source", "copy", "final"):
            with self.subTest(mutation=mutation), self.native_fixture(mutation=mutation):
                with self.assertRaisesRegex(ValueError, "changed app"):
                    bundle.package("macos-arm64", "1.10.0", self.output / mutation)
            report = self.report()
            difference = report["diff" if mutation == "final" else "copy_diff"]
            self.assertEqual(sum(difference["counts"].values()), 1)
            if mutation != "source":
                self.assertEqual(difference["removed"], ["._metadata.json"])
            self.assertNotIn("smoke", self.events)
            self.assertFalse((self.output / mutation / "manifest.json").exists())
            self.assertFalse(self.workspace.exists())
            self.resource.write_bytes(b"original signed resource")
            self.events.clear()

    def test_native_failures_are_fatal_and_durable(self):
        for failure in ("pre-image-verify", "create-image", "attach-writable", "copy-image-verify",
                        "convert-image", "attach-readonly", "post-image-verify"):
            with self.subTest(failure=failure), self.native_fixture(failures={failure}):
                with self.assertRaises(subprocess.CalledProcessError) as raised:
                    bundle.package("macos-arm64", "1.10.0", self.output / failure)
                self.assertIs(raised.exception, self.native_error)
            report = self.report()
            self.assertEqual(report["stage"], failure)
            self.assertEqual(report["errors"][-1]["stdout"], "native stdout")
            self.assertEqual(report["errors"][-1]["stderr"], "sealed resource invalid")
            self.assertNotIn("smoke", self.events)
            self.assertFalse((self.output / failure / "manifest.json").exists())
            self.assertFalse(self.workspace.exists())
            if failure.startswith("attach-"):
                self.assertEqual(self.events[-1], failure.replace("attach-", "detach-"))
            self.events.clear()

    def test_detach_failure_retains_workspace_and_never_masks_original(self):
        cases = ({"copy-image-verify", "detach-writable"}, {"detach-writable"},
                 {"post-image-verify", "detach-readonly"}, {"detach-readonly"},
                 {"attach-writable", "detach-writable"})
        for index, failures in enumerate(cases):
            with self.subTest(failures=failures), self.native_fixture(failures=failures) as cleanup:
                with self.assertRaises(subprocess.CalledProcessError) as raised:
                    bundle.package("macos-arm64", "1.10.0", self.output / str(index))
                self.assertIs(raised.exception, self.native_error if len(failures) > 1 else self.detach_error)
                self.assertFalse(any(Path(call.args[0]) == self.workspace for call in cleanup.call_args_list))
            report = self.report()
            self.assertEqual(report["retained_paths"], [str(self.workspace)])
            self.assertTrue(self.workspace.is_dir())
            self.assertTrue(any(error.get("stderr") == "Resource busy" for error in report["errors"]))
            self.assertFalse((self.output / str(index) / "manifest.json").exists())

    def test_invalid_attach_plist_rolls_back(self):
        with self.native_fixture(malformed=True), self.assertRaises(plistlib.InvalidFileException):
            bundle.package("macos-arm64", "1.10.0", self.output)
        self.assertEqual(self.events[-1], "detach-writable")
        self.assertFalse(self.workspace.exists())
        self.assertEqual(self.report()["stage"], "attach-writable")

    def test_cleanup_error_is_reported_without_replacing_gate_error(self):
        with self.native_fixture(failures={"pre-image-verify"}, cleanup_failure=True):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                bundle.package("macos-arm64", "1.10.0", self.output)
            self.assertIs(raised.exception, self.native_error)
        report = self.report()
        self.assertEqual(report["errors"][0]["stage"], "cleanup")
        self.assertEqual(report["retained_paths"], [str(self.workspace)])

    def test_failed_smoke_detaches_and_blocks_artifact_manifest(self):
        with self.native_fixture(failures={"smoke"}):
            with self.assertRaisesRegex(RuntimeError, "Frozen executable exited 1"):
                bundle.package("macos-arm64", "1.10.0", self.output)
        self.assertEqual(self.events[-2:], ["smoke", "detach-readonly"])
        self.assertEqual(self.report()["stage"], "smoke")
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertFalse(self.workspace.exists())

    def test_cleanup_failure_alone_is_fatal(self):
        with self.native_fixture(cleanup_failure=True):
            with self.assertRaisesRegex(PermissionError, "cleanup unavailable"):
                bundle.package("macos-arm64", "1.10.0", self.output)
        self.assertEqual(self.report()["stage"], "cleanup")
        self.assertFalse((self.output / "manifest.json").exists())

    def test_image_capacity_covers_allocation_expansion_and_rounds_up(self):
        entries = [SimpleNamespace(st_blocks=8, st_size=128),
                   SimpleNamespace(st_blocks=8, st_size=8 * 1024 ** 2 + 1),
                   SimpleNamespace(st_blocks=32768, st_size=1024 ** 2),
                   SimpleNamespace(st_blocks=0, st_size=1)]
        with patch.object(Path, "rglob", return_value=iter([Path("compressed"), Path("allocated"), Path("tiny")])), \
                patch.object(Path, "lstat", side_effect=entries):
            self.assertEqual(bundle.macos_image_size_mib(self.root), 285)

    def test_timeout_output_bytes_are_json_serializable_and_rollback_runs(self):
        report_path = self.base / "smoke-package-macos-arm64.json"
        error = subprocess.TimeoutExpired(["hdiutil", "attach"], 120, output=b"partial mount", stderr=b"busy")
        with patch.dict(os.environ, {"RUNNER_TEMP": str(self.base)}):
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                with bundle.macos_package_report("macos-arm64") as diagnostics:
                    with patch.object(bundle.subprocess, "run", side_effect=[error,
                            subprocess.CompletedProcess([], 0, "detached", "")]) as commands:
                        with bundle.mounted_macos_image(Path("fixture.dmg"), self.base / "mount", diagnostics):
                            self.fail("timed-out attach yielded a mount")
            self.assertIs(raised.exception, error)
        self.assertEqual(commands.call_args_list[-1].args[0][1], "detach")
        report = json.loads(report_path.read_text())
        self.assertEqual(report["errors"][-1]["stdout"], "partial mount")

    def test_manifest_equal_and_added_removed_changed(self):
        before = bundle.browser_tree_manifest(self.root, all_modes=True)
        copied = self.base / "copied"
        shutil.copytree(self.root, copied)
        self.assertEqual(bundle.manifest_difference(before, bundle.browser_tree_manifest(
            copied, all_modes=True))["counts"], dict(added=0, removed=0, changed=0))
        self.resource.write_bytes(b"changed")
        (self.root / "._ordinary").unlink()
        (self.root / "added").mkdir()
        after = bundle.browser_tree_manifest(self.root, all_modes=True)
        difference = bundle.manifest_difference(before, after)
        self.assertEqual(difference["counts"], dict(added=1, removed=1, changed=1))
        self.assertEqual(difference["added"], ["added"])
        self.assertEqual(difference["removed"], ["._ordinary"])
        self.assertEqual(difference["changed"]["resource"],
                         dict(before=before["resource"], after=after["resource"]))

    def test_diff_tracks_modes_types_and_link_targets(self):
        before = {"mode": ("file", 0o755, "hash"), "type": ("directory", 0o755),
                  "link": ("link", "first", 0o777)}
        after = {"mode": ("file", 0o644, "hash"), "type": ("file", 0o755, "hash"),
                 "link": ("link", "second", 0o777)}
        self.assertEqual(bundle.manifest_difference(before, after)["counts"],
                         dict(added=0, removed=0, changed=3))

    @unittest.skipIf(sys.platform == "win32", "requires POSIX symlinks and modes")
    def test_real_copy_preserves_modes_symlinks_and_literal_dot_underscore(self):
        directory = self.root / "directory"
        directory.mkdir()
        (directory / "file").write_bytes(b"fixture")
        (self.root / "alias").symlink_to("directory", target_is_directory=True)
        (self.root / "recursive").symlink_to(".", target_is_directory=True)
        directory.chmod(0o700)
        (directory / "file").chmod(0o751)
        before = bundle.browser_tree_manifest(self.root, all_modes=True)
        self.assertEqual(before["alias"][:2], ("link", "directory"))
        self.assertNotIn("alias/file", before)
        self.assertEqual(before["directory"][1], 0o700)
        self.assertEqual(before["directory/file"][1], 0o751)
        with self.native_fixture():
            bundle.package("macos-arm64", "1.10.0", self.output)
        self.assertEqual(self.report()["manifests"]["before"], self.report()["manifests"]["after"])

    def test_report_write_failure_does_not_replace_original_error(self):
        original = subprocess.CalledProcessError(1, ["codesign"])
        with patch.object(Path, "write_text", side_effect=PermissionError("report unavailable")), \
                patch("builtins.print") as warning:
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                with bundle.macos_package_report("macos-x64"):
                    raise original
        self.assertIs(raised.exception, original)
        warning.assert_called_once_with(
            "Could not persist macOS package diagnostics: report unavailable", file=sys.stderr)

    def test_package_diagnostics_require_native_macos(self):
        with patch.object(bundle.sys, "platform", "win32"), patch.object(bundle.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "native macOS"):
                bundle.package_macos(Path("Omni-OS.app"), Path("bundle.dmg"), "macos-x64")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
