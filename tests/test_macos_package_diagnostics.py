"""Packaging instrumentation with synthetic trees and mocked native commands."""

from contextlib import chdir, contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import release_bundle as bundle


class MacPackageDiagnosticsTests(unittest.TestCase):
    def test_manifest_equal_and_added_removed_changed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "original"
            root.mkdir()
            (root / "resource").write_bytes(b"original fixture")
            (root / "removed").mkdir()
            before = bundle.browser_tree_manifest(root, all_modes=True)
            copied = Path(temporary) / "copied"
            shutil.copytree(root, copied)
            self.assertEqual(bundle.manifest_difference(
                before, bundle.browser_tree_manifest(copied, all_modes=True))["counts"],
                             {"added": 0, "removed": 0, "changed": 0})
            (root / "resource").write_bytes(b"changed fixture")
            (root / "removed").rmdir()
            (root / "added").mkdir()
            after = bundle.browser_tree_manifest(root, all_modes=True)
            diff = bundle.manifest_difference(before, after)
            self.assertEqual(diff["counts"], {"added": 1, "removed": 1, "changed": 1})
            self.assertEqual(diff["added"], ["added"])
            self.assertEqual(diff["removed"], ["removed"])
            self.assertEqual(diff["changed"]["resource"],
                             {"before": before["resource"], "after": after["resource"]})
            self.assertEqual(len(after["added"]), 2)
            self.assertNotIn("changed fixture", json.dumps(after))

    def test_diff_tracks_modes_types_and_link_targets(self):
        before = {"mode": ("file", 0o755, "hash"), "type": ("directory", 0o755),
                  "link": ("link", "first", 0o777)}
        after = {"mode": ("file", 0o644, "hash"), "type": ("file", 0o755, "hash"),
                 "link": ("link", "second", 0o777)}
        self.assertEqual(bundle.manifest_difference(before, after)["counts"],
                         {"added": 0, "removed": 0, "changed": 3})

    @unittest.skipIf(sys.platform == "win32", "requires POSIX symlinks and modes")
    def test_manifest_does_not_recurse_through_links_and_tracks_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "directory"
            directory.mkdir()
            (directory / "file").write_bytes(b"fixture")
            (root / "alias").symlink_to("directory", target_is_directory=True)
            (root / "recursive").symlink_to(".", target_is_directory=True)
            before = bundle.browser_tree_manifest(root, all_modes=True)
            self.assertEqual(set(before), {".", "directory", "directory/file", "alias", "recursive"})
            self.assertEqual(before["alias"][:2], ("link", "directory"))
            directory.chmod(0o700)
            (directory / "file").chmod(0o700)
            after = bundle.browser_tree_manifest(root, all_modes=True)
            self.assertEqual(after["directory"][1], 0o700)
            self.assertEqual(after["directory/file"][1], 0o700)

    def test_package_reports_success_and_failures_before_cleanup(self):
        for target in ("macos-x64", "macos-arm64"):
            for failure in (None, "pre-zip-verify", "extract", "post-zip-verify"):
                with self.subTest(target=target, failure=failure), tempfile.TemporaryDirectory() as temporary:
                    base = Path(temporary).resolve()
                    root = base / "dist/Omni-OS.app"
                    root.mkdir(parents=True)
                    resource = root / "resource"
                    resource.write_bytes(b"before ZIP fixture")
                    original = bundle.browser_tree_manifest(root, all_modes=True)
                    report_path = base / f"smoke-package-{target}.json"
                    output = base / "release-assets"
                    extracted_paths = []
                    stages = []
                    native_error = subprocess.CalledProcessError(
                        1, ["native fixture"], output="native stdout", stderr="a sealed resource is missing or invalid")
                    real_temporary_directory = tempfile.TemporaryDirectory

                    @contextmanager
                    def extraction_directory(**kwargs):
                        with real_temporary_directory(**kwargs) as extracted:
                            if kwargs.get("prefix") != "omni-final-package-":
                                yield extracted
                                return
                            extracted_paths.append(Path(extracted))
                            try:
                                yield extracted
                            finally:
                                # The durable report must exist while the app
                                # still exists, not just after cleanup.
                                self.assertTrue(report_path.is_file())

                    def native_run(command, **kwargs):
                        self.assertTrue(kwargs["check"])
                        if command[0] == "/usr/bin/codesign":
                            stage = "post-zip-verify" if "extract" in stages else "pre-zip-verify"
                            self.assertEqual(command[1:-1], ["--verify", "--deep", "--strict", "--verbose=4"])
                            self.assertTrue(kwargs["capture_output"])
                            self.assertTrue(kwargs["text"])
                        elif "-c" in command:
                            stage = "archive"
                            Path(command[-1]).write_bytes(b"synthetic ZIP; native ditto is mocked")
                            # Model a mutation during archiving: comparing the
                            # source only after ZIP would miss this difference.
                            resource.write_bytes(b"after ZIP fixture")
                        else:
                            stage = "extract"
                        stages.append(stage)
                        if failure == stage:
                            raise native_error
                        if stage == "extract":
                            shutil.copytree(root, Path(command[-1]) / root.name)
                        return subprocess.CompletedProcess(command, 0, stage + " stdout", stage + " stderr")

                    with chdir(base), patch.dict(os.environ, {"RUNNER_TEMP": str(base),
                            "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123"}), \
                            patch.object(bundle.sys, "platform", "darwin"), \
                            patch.object(bundle.platform, "system", return_value="Darwin"), \
                            patch.object(bundle.platform, "machine", return_value="x86_64" if target == "macos-x64" else "arm64"), \
                            patch.object(bundle.tempfile, "TemporaryDirectory", side_effect=extraction_directory), \
                            patch.object(bundle.subprocess, "run", side_effect=native_run), \
                            patch.object(bundle.subprocess, "Popen") as popen, \
                            patch.object(bundle, "archive_members_safe"), \
                            patch.object(bundle, "verify_report"), \
                            patch.object(bundle, "publish") as publish:
                        popen.return_value.wait.return_value = 0
                        if failure:
                            with self.assertRaises(subprocess.CalledProcessError) as raised:
                                bundle.package(target, "1.10.0", output)
                            self.assertIs(raised.exception, native_error)
                            popen.assert_not_called()
                            self.assertFalse((output / "manifest.json").exists())
                        else:
                            bundle.package(target, "1.10.0", output)
                            popen.assert_called_once()
                            self.assertTrue((output / "manifest.json").is_file())
                        publish.assert_not_called()
                    self.assertTrue(all(not path.exists() for path in extracted_paths))
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    self.assertEqual(report["target"], target)
                    self.assertEqual(report["stage"], failure or "complete")
                    self.assertIn("xattrs", report["limitations"])
                    if failure:
                        self.assertEqual(report["errors"][0]["returncode"], 1)
                        self.assertEqual(report["errors"][0]["stdout"], "native stdout")
                        self.assertEqual(report["errors"][0]["stderr"], native_error.stderr)
                    expected_stages = ["pre-zip-verify", "archive", "extract", "post-zip-verify"]
                    self.assertEqual(stages, expected_stages[:expected_stages.index(failure) + 1]
                                     if failure else expected_stages)
                    if failure == "extract":
                        self.assertIsNone(report["diff"])
                        self.assertEqual(report["manifests"]["before"]["resource"], list(original["resource"]))
                    if failure in (None, "post-zip-verify"):
                        self.assertEqual(report["diff"]["counts"], {"added": 0, "removed": 0, "changed": 1})
                        self.assertEqual(report["manifests"]["before"]["resource"], list(original["resource"]))
                        post = report["native_verify"]["post-zip-verify"]
                        self.assertEqual(post["returncode"], 1 if failure else 0)
                        self.assertIn("stdout", post["stdout"])
                        self.assertTrue(post["stderr"])
                    pre = report["native_verify"]["pre-zip-verify"]
                    self.assertEqual(pre["returncode"], 1 if failure == "pre-zip-verify" else 0)
                    self.assertNotIn("before ZIP fixture", report_path.read_text(encoding="utf-8"))

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
                bundle.package_macos(Path("Omni-OS.app"), Path("bundle.zip"), "macos-x64")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
