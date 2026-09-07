"""Offline stdlib tests for release gates; never import/start the application."""

import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import release_bundle as bundle


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_strict_tag_versions(self):
        for tag in ("v0.0.0", "v1.10.0", "v123.4.56"):
            self.assertEqual(bundle.version_from_env({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": tag}), tag[1:])
        for tag in ("v01.2.3", "v1.2", "v1.2.3-rc1", "v1.2.3\n", "v1.2.3/x", "v1.2.$(whoami)", "V1.2.3", "v١.2.3"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                bundle.version_from_env({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": tag})
        self.assertEqual(bundle.version_from_env({"GITHUB_RUN_NUMBER": "15"}), "0.0.15")

    def test_expected_five_distinct_assets(self):
        names = [name for target in bundle.TARGETS for name in bundle.asset_names(target, "1.10.0")]
        self.assertEqual(len(set(names)), 5)
        with self.assertRaises(ValueError):
            bundle.asset_names("macos-universal", "1.10.0")

    def release_env(self, manual=False):
        return {"GITHUB_EVENT_NAME": "workflow_dispatch" if manual else "push",
                "GITHUB_REF_TYPE": "branch" if manual else "tag",
                "GITHUB_REF_NAME": "main" if manual else "v1.10.0",
                "GITHUB_REF": "refs/heads/main" if manual else "refs/tags/v1.10.0",
                "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_NUMBER": "15"}

    def tag_response(self, sha="a" * 40, kind="commit"):
        return "HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + json.dumps({
            "ref": "refs/tags/v1.10.0", "object": {"type": kind, "sha": sha, "url": "fixture"}})

    def missing_tag(self):
        return subprocess.CalledProcessError(1, ["gh", "api"],
                                             output='HTTP/2.0 404 Not Found\r\n\r\n{"message":"Not Found"}')

    def test_dispatch_is_not_authorized_to_release(self):
        env = self.release_env(manual=True)
        self.assertEqual(bundle.version_from_env(env), "0.0.15")
        with self.assertRaises(ValueError):
            bundle.release_version_from_env(env)

    def test_release_tag_version_is_strict(self):
        env = self.release_env()
        self.assertEqual(bundle.release_version_from_env(env), "1.10.0")
        for tag in ("v01.2.3", "1.2.3", "v1.2", "v1.2.3-rc1", "v1.2.3\n",
                    " v1.2.3", "v1.2.3/x", "v1.2.$(whoami)", "V1.2.3", "v١.2.3"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                bundle.release_version_from_env(dict(env, GITHUB_REF_NAME=tag, GITHUB_REF="refs/tags/" + tag))

    def test_release_requires_matching_tag_ref(self):
        for overrides in ({"GITHUB_REF": "refs/heads/v1.10.0"}, {"GITHUB_REF": ""},
                          {"GITHUB_REF_NAME": "v9.9.9"}, {"GITHUB_REF_TYPE": "branch"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                bundle.release_version_from_env(dict(self.release_env(), **overrides))

    def test_non_tag_push_events_are_tests_only(self):
        for event in ("push", "workflow_dispatch", "pull_request", "workflow_run", ""):
            for tag in (False, True):
                if event == "push" and tag:
                    continue
                env = dict(self.release_env(manual=not tag), GITHUB_EVENT_NAME=event)
                for command in ("verify", "publish"):
                    with self.subTest(event=event, tag=tag, command=command), \
                            patch.dict(os.environ, env, clear=True), \
                            patch.object(sys, "argv", ["release_bundle.py", command]), \
                            patch.object(bundle, "verify_artifacts") as verify, \
                            patch.object(bundle, "publish") as publish, self.assertRaises(ValueError):
                        bundle.main()
                    verify.assert_not_called()
                    publish.assert_not_called()

    def test_tag_push_cli_verifies_artifact_version(self):
        downloads = self.fixtures()
        with patch.dict(os.environ, self.release_env(), clear=True), \
                patch.object(sys, "argv", ["release_bundle.py", "verify", "--downloads", str(downloads),
                                           "--output", str(self.root / "release")]):
            bundle.main()
        self.assertTrue((self.root / "release/Omni-OS-Setup-1.10.0.exe").is_file())

    def test_workflow_build_and_release_require_new_tag_push(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        gate = "github.event_name == 'push' && github.event.created == true && startsWith(github.ref, 'refs/tags/v')"
        for job, condition in (("build", gate), ("release", "needs.build.result == 'success' && " + gate)):
            section = workflow.split(f"\n  {job}:\n", 1)[1]
            self.assertEqual(section.split("\n    if: ", 1)[1].splitlines()[0], condition)
        self.assertIn("  workflow_dispatch:\n\npermissions:", workflow)

    def test_privacy_all_resource_roots(self):
        for prefix in ("", "_internal", "Contents/Resources", "Contents/Frameworks", "Contents/MacOS"):
            for suffix in ("config/settings.json", "memory/history.txt", "wallet/key.txt", "logs/session.log", ".env", "api_keys.json"):
                with self.subTest(prefix=prefix, suffix=suffix):
                    path = self.root / prefix / suffix
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("private fixture", encoding="utf-8")
                    with self.assertRaises(ValueError):
                        bundle.privacy(self.root)
                    path.unlink()

    def test_library_metadata_and_config_are_allowed(self):
        for name in ("package/config/schema.json", "package-1.0.dist-info/metadata.json",
                     "Contents/Resources/library/config/default.json"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        bundle.privacy(self.root)

    def test_zip_traversal_and_external_symlink_rejected(self):
        for name in ("../secret", "/tmp/secret", "C:/secret", "folder\\secret", "folder\0secret"):
            path = self.root / "unsafe.zip"
            with zipfile.ZipFile(path, "w") as archive:
                # ZipInfo's constructor normalizes Windows separators; assign
                # after construction to model a hostile archive's raw filename.
                entry = zipfile.ZipInfo("placeholder")
                entry.filename = name
                archive.writestr(entry, "bad")
            with self.subTest(name=name):
                with zipfile.ZipFile(path) as archive:
                    entry, = archive.infolist()
                    self.assertEqual(entry.orig_filename, name)
                    self.assertEqual(entry.filename, name.split("\0", 1)[0].replace(os.sep, "/"))
                with self.assertRaises(ValueError):
                    bundle.archive_members_safe(path)
        # A genuine forward-slash name is safe, unlike a normalized raw backslash.
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("folder/secret", "safe")
        bundle.archive_members_safe(path)
        with zipfile.ZipFile(path, "w") as archive:
            entry = zipfile.ZipInfo("Omni-OS.app/Contents/Resources/link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../../../../outside")
        with self.assertRaises(ValueError):
            bundle.archive_members_safe(path)

    def test_mac_internal_symlink_is_allowed(self):
        path = self.root / "mac.zip"
        with zipfile.ZipFile(path, "w") as archive:
            entry = zipfile.ZipInfo("Omni-OS.app/Contents/Resources/package")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../Frameworks/package")
            archive.writestr("Omni-OS.app/Contents/Frameworks/package/module", "test")
        bundle.archive_members_safe(path)

    def graph_archive(self, archive_format, entries):
        path = self.root / ("graph.zip" if archive_format == "zip" else "graph.tar.gz")
        if archive_format == "zip":
            with zipfile.ZipFile(path, "w") as archive:
                for name, kind, target in entries:
                    entry = zipfile.ZipInfo(name)
                    entry.create_system = 3
                    mode = {"link": stat.S_IFLNK, "directory": stat.S_IFDIR, "file": stat.S_IFREG}[kind]
                    entry.external_attr = (mode | 0o777) << 16
                    archive.writestr(entry, target if kind == "link" else "" if kind == "directory" else "fixture")
        else:
            with tarfile.open(path, "w:gz") as archive:
                for name, kind, target in entries:
                    entry = tarfile.TarInfo(name)
                    if kind in {"link", "hardlink"}:
                        entry.type = tarfile.SYMTYPE if kind == "link" else tarfile.LNKTYPE
                        entry.linkname = target
                        archive.addfile(entry)
                    elif kind == "directory":
                        entry.type = tarfile.DIRTYPE
                        archive.addfile(entry)
                    else:
                        entry.size = 7
                        archive.addfile(entry, io.BytesIO(b"fixture"))
        return path

    def test_archive_link_graph_composed_escape_rejected(self):
        entries = [("Omni-OS.app/back", "link", ".."),
                   ("Omni-OS.app/out", "link", "back/..")]
        for archive_format in ("zip", "tar"):
            for payload in ([], [("Omni-OS.app/out/payload", "file", "")]):
                with self.subTest(format=archive_format, payload=bool(payload)):
                    path = self.graph_archive(archive_format, entries + payload)
                    with self.assertRaises(ValueError):
                        bundle.archive_members_safe(path)

    def test_archive_link_ancestors_rejected_in_either_order(self):
        for archive_format in ("zip", "tar"):
            entries = [("Omni-OS.app/alias", "link", "Contents"),
                       ("Omni-OS.app/alias/payload", "file", "")]
            for ordered in (entries, entries[::-1]):
                with self.subTest(format=archive_format, first=ordered[0][0]):
                    path = self.graph_archive(archive_format, ordered)
                    with self.assertRaises(ValueError):
                        bundle.archive_members_safe(path)

    def test_archive_link_cycles_rejected(self):
        for archive_format in ("zip", "tar"):
            for target in ("a", "b"):
                with self.subTest(format=archive_format, target=target):
                    path = self.graph_archive(archive_format, [
                        ("Omni-OS.app/a", "link", target),
                        ("Omni-OS.app/b", "link", "a"),
                    ])
                    with self.assertRaises(ValueError):
                        bundle.archive_members_safe(path)

    def test_archive_duplicate_paths_and_file_ancestors_rejected(self):
        for archive_format in ("zip", "tar"):
            for name, kind, target in (("Omni-OS.app/./item", "file", ""),
                                       ("Omni-OS.app/./item", "link", "Contents"),
                                       ("Omni-OS.app/item/", "directory", ""),
                                       ("Omni-OS.app/item/payload", "file", "")):
                with self.subTest(format=archive_format, name=name, kind=kind):
                    path = self.graph_archive(archive_format, [
                        ("Omni-OS.app/item", "file", ""), (name, kind, target),
                    ])
                    with self.assertRaises(ValueError):
                        bundle.archive_members_safe(path)

    def test_archive_link_expansion_is_bounded(self):
        for archive_format in ("zip", "tar"):
            for branching in (False, True):
                entries = [("Omni-OS.app/a0", "link", ".")]
                for index in range(1, 43 if not branching else 13):
                    target = f"a{index - 1}"
                    entries.append((f"Omni-OS.app/a{index}", "link",
                                    f"{target}/{target}" if branching else target))
                with self.subTest(format=archive_format, branching=branching):
                    path = self.graph_archive(archive_format, entries)
                    with self.assertRaises(ValueError):
                        bundle.archive_members_safe(path)

    def test_archive_framework_link_graph_is_allowed(self):
        entries = [("Omni-OS.app/Contents/Frameworks/Kit/Versions/A/Kit", "file", ""),
                   ("Omni-OS.app/Contents/Frameworks/Kit/Versions/A/Resources/data", "file", ""),
                   ("Omni-OS.app/Contents/Frameworks/Kit/Versions/Current", "link", "A"),
                   ("Omni-OS.app/Contents/Frameworks/Kit/Kit", "link", "Versions/Current/Kit"),
                   ("Omni-OS.app/Contents/Frameworks/Kit/Resources", "link", "Versions/Current/Resources"),
                   ("Omni-OS.app/Contents/MacOS/resources", "link", "../Resources"),
                   ("Omni-OS.app/Contents/Resources/data", "file", "")]
        for archive_format in ("zip", "tar"):
            with self.subTest(format=archive_format):
                bundle.archive_members_safe(self.graph_archive(archive_format, entries))

    def test_tar_hardlink_graph_is_validated(self):
        for target in ("Omni-OS.app/a", "Omni-OS.app/back/.."):
            with self.subTest(target=target):
                path = self.graph_archive("tar", [
                    ("Omni-OS.app/back", "link", ".."),
                    ("Omni-OS.app/a", "hardlink", target),
                ])
                with self.assertRaises(ValueError):
                    bundle.archive_members_safe(path)
        bundle.archive_members_safe(self.graph_archive("tar", [
            ("Omni-OS/payload", "file", ""),
            ("Omni-OS/alias", "hardlink", "Omni-OS/payload"),
        ]))

    def test_tar_special_files_and_link_escape_rejected(self):
        path = self.root / "unsafe.tar.gz"
        for kind, target in ((tarfile.SYMTYPE, "../../outside"), (tarfile.LNKTYPE, "../outside"), (tarfile.CHRTYPE, "")):
            with tarfile.open(path, "w:gz") as archive:
                entry = tarfile.TarInfo("Omni-OS/link")
                entry.type = kind
                entry.linkname = target
                archive.addfile(entry)
            with self.assertRaises(ValueError):
                bundle.archive_members_safe(path)

    def fixtures(self):
        downloads = self.root / "downloaded"
        downloads.mkdir()
        for target in bundle.TARGETS:
            directory = downloads / f"bundle-123-{target}"
            directory.mkdir()
            manifest = dict(target=target, version="1.10.0", commit="a" * 40, run_id="123", assets={})
            for name in bundle.asset_names(target, "1.10.0"):
                path = directory / name
                if name.endswith(".exe"):
                    path.write_bytes(b"MZfixture")
                elif name.endswith(".zip"):
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr("Omni-OS/fixture", "fixture")
                else:
                    with tarfile.open(path, "w:gz") as archive:
                        entry = tarfile.TarInfo("Omni-OS/fixture")
                        entry.size = 7
                        archive.addfile(entry, io.BytesIO(b"fixture"))
                manifest["assets"][name] = dict(sha256=bundle.digest(path), size=path.stat().st_size)
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return downloads

    def verify(self, downloads):
        bundle.verify_artifacts(downloads, self.root / "release", "1.10.0", "a" * 40, "123")

    def test_complete_release_and_aggregate_checksums(self):
        self.verify(self.fixtures())
        self.assertEqual(len(list((self.root / "release").iterdir())), 6)
        sums = (self.root / "release/SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(sums), 5)
        names = [line.split("  ")[1] for line in sums]
        self.assertEqual(names, sorted(names))  # Same ordering on Windows and POSIX.
        for line in sums:
            checksum, name = line.split("  ")
            self.assertEqual(checksum, bundle.digest(self.root / "release" / name))

    def test_missing_target_blocks_release(self):
        downloads = self.fixtures()
        (downloads / "bundle-123-macos-arm64").rename(downloads / "wrong-run-macos-arm64")
        with self.assertRaises(ValueError):
            self.verify(downloads)

    def test_identity_mismatch_blocks_release(self):
        downloads = self.fixtures()
        path = downloads / "bundle-123-linux-x64/manifest.json"
        original = json.loads(path.read_text())
        for key in ("target", "version", "commit", "run_id"):
            manifest = dict(original, **{key: "wrong"})
            path.write_text(json.dumps(manifest))
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(downloads)

    def test_tampered_or_extra_asset_blocks_release(self):
        downloads = self.fixtures()
        extra = downloads / "bundle-123-windows-x64/unexpected.exe"
        extra.write_bytes(b"MZ")
        with self.assertRaises(ValueError):
            self.verify(downloads)
        extra.unlink()
        path = downloads / "bundle-123-windows-x64/Omni-OS-Setup-1.10.0.exe"
        path.write_bytes(b"MZchanged")
        with self.assertRaises(ValueError):
            self.verify(downloads)

    def test_oversize_asset_blocks_release(self):
        with patch.object(bundle, "MAX_ASSET", 5), self.assertRaises(ValueError):
            self.verify(self.fixtures())

    def test_all_seven_checks_and_xcb_are_required(self):
        path = self.root / "report.json"
        result = dict(ok=True, checks=sorted(bundle.CHECKS), qt_platform="xcb")
        path.write_text(json.dumps(result))
        bundle.verify_report(path, "xcb")
        with self.assertRaises(ValueError):
            bundle.verify_report(path, "offscreen")
        result["checks"].remove("browser_webkit")
        path.write_text(json.dumps(result))
        with self.assertRaises(ValueError):
            bundle.verify_report(path)

    def test_clean_environment_does_not_inherit_builder_or_keys(self):
        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "builder", "PYTHONPATH": "builder",
                                     "LD_LIBRARY_PATH": "builder", "GH_TOKEN": "fixture", "GEMINI_API_KEY": "fixture"}):
            env = bundle.clean_environment(self.root)
        for name in ("PLAYWRIGHT_BROWSERS_PATH", "PYTHONPATH", "LD_LIBRARY_PATH", "GH_TOKEN", "GEMINI_API_KEY"):
            self.assertNotIn(name, env)
        self.assertEqual(env["HOME"], str(self.root))

    def test_baseline_does_not_exclude_browser_or_cpp_libraries(self):
        for name in ("libc.so.6", "libGL.so.1", "libEGL.so.1", "ld-linux-x86-64.so.2"):
            self.assertIsNotNone(bundle.BASELINE.fullmatch(name))
        for name in ("libstdc++.so.6", "libnss3.so", "libGLESv2.so", "libvk_swiftshader.so", "libgtk-3.so.0", "libportaudio.so.2"):
            self.assertIsNone(bundle.BASELINE.fullmatch(name))

    def test_ldd_missing_dependency_fails(self):
        result = subprocess.CompletedProcess([], 0, "libfoo.so => not found\n", "")
        with patch.object(bundle.subprocess, "run", return_value=result), self.assertRaises(RuntimeError):
            bundle.ldd_dependencies(Path("trusted-fixture"))

    def test_existing_release_or_draft_never_overwritten(self):
        self.verify(self.fixtures())
        releases = json.dumps([[{"tag_name": "v1.10.0", "draft": True}]])
        with patch.dict(os.environ, self.release_env(), clear=True), \
                patch.object(bundle.subprocess, "check_output", return_value=releases) as api, \
                patch.object(bundle.subprocess, "run") as run, self.assertRaises(ValueError):
            bundle.publish(self.root / "release", "1.10.0")
        self.assertEqual(api.call_count, 1)  # Reject before even looking up the tag.
        run.assert_not_called()

    def test_moved_remote_tag_blocks_publication(self):
        self.verify(self.fixtures())
        with patch.dict(os.environ, self.release_env(), clear=True), \
                patch.object(bundle.subprocess, "check_output", side_effect=["[]", self.tag_response("b" * 40)]), \
                patch.object(bundle.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "no longer points"):
            bundle.publish(self.root / "release", "1.10.0")
        run.assert_not_called()

    def test_publish_verifies_existing_tag_and_draft_without_creating_tag(self):
        self.verify(self.fixtures())
        output = self.root / "release"
        for mode in ("tag-existing", "tag-annotated"):
            responses = ["[]"]
            responses.append(self.tag_response(kind="tag" if mode == "tag-annotated" else "commit"))
            if mode == "tag-annotated":
                responses.append(json.dumps({"object": {"type": "commit", "sha": "a" * 40, "url": "fixture"}}))
            events = []

            def api(command, **kwargs):
                events.append(command)
                result = responses.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            def run(command, **kwargs):
                events.append(command)
                if command[:3] == ["gh", "release", "download"]:
                    for path in output.iterdir():
                        shutil.copy2(path, Path(command[-1]) / path.name)

            with self.subTest(mode=mode), patch.dict(os.environ, self.release_env(), clear=True), \
                    patch.object(bundle.subprocess, "check_output", side_effect=api), \
                    patch.object(bundle.subprocess, "run", side_effect=run):
                bundle.publish(output, "1.10.0")
            self.assertFalse(any("--method" in command for command in events))
            self.assertEqual([command[2] for command in events if command[:2] == ["gh", "release"]],
                             ["create", "download", "edit"])
            draft = next(command for command in events if command[:3] == ["gh", "release", "create"])
            self.assertIn("--verify-tag", draft)
            self.assertIn("--draft", draft)
            self.assertEqual(events[-1], ["gh", "release", "edit", "v1.10.0", "--draft=false"])
            self.assertEqual(responses, [])

    def test_missing_triggered_tag_is_never_recreated(self):
        self.verify(self.fixtures())
        with patch.dict(os.environ, self.release_env(), clear=True), \
                patch.object(bundle.subprocess, "check_output", side_effect=["[]", self.missing_tag()]), \
                patch.object(bundle.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "never recreate"):
            bundle.publish(self.root / "release", "1.10.0")
        run.assert_not_called()

    def test_incomplete_or_changed_draft_is_never_published(self):
        self.verify(self.fixtures())
        output = self.root / "release"
        for mode in ("missing", "changed"):
            def run(command, **kwargs):
                if command[:3] == ["gh", "release", "download"]:
                    for path in output.iterdir():
                        shutil.copy2(path, Path(command[-1]) / path.name)
                    asset = Path(command[-1]) / "Omni-OS-Setup-1.10.0.exe"
                    if mode == "missing":
                        asset.unlink()
                    else:
                        asset.write_bytes(b"MZtampered")

            with self.subTest(mode=mode), patch.dict(os.environ, self.release_env(), clear=True), \
                    patch.object(bundle.subprocess, "check_output", side_effect=["[]", self.tag_response()]), \
                    patch.object(bundle.subprocess, "run", side_effect=run) as commands, \
                    self.assertRaisesRegex(ValueError, "manual recovery required"):
                bundle.publish(output, "1.10.0")
            self.assertEqual([call.args[0][:3] for call in commands.call_args_list],
                             [["gh", "release", "create"], ["gh", "release", "download"]])

    def test_tag_lookup_errors_are_not_missing(self):
        self.verify(self.fixtures())
        for output in ('HTTP/2.0 401 Unauthorized\n\n{"message":"404"}',
                       'HTTP/2.0 403 Forbidden\n\n{}', 'HTTP/2.0 500 Server Error\n\n{}',
                       'gh: authentication failed (HTTP 404)', ''):
            error = subprocess.CalledProcessError(1, ["gh", "api"], output=output)
            with self.subTest(output=output), patch.dict(os.environ, self.release_env(), clear=True), \
                    patch.object(bundle.subprocess, "check_output", side_effect=["[]", error]), \
                    patch.object(bundle.subprocess, "run") as run, self.assertRaises(subprocess.CalledProcessError):
                bundle.publish(self.root / "release", "1.10.0")
            run.assert_not_called()

    def test_missing_tag_is_not_retried_or_written(self):
        self.verify(self.fixtures())
        with patch.dict(os.environ, self.release_env(), clear=True), \
                patch.object(bundle.subprocess, "check_output", side_effect=["[]", self.missing_tag()]) as api, \
                patch.object(bundle.subprocess, "run") as run, self.assertRaises(ValueError):
            bundle.publish(self.root / "release", "1.10.0")
        run.assert_not_called()
        self.assertEqual(api.call_count, 2)

    def test_malformed_tag_lookup_never_authorizes_publication(self):
        self.verify(self.fixtures())
        for response in (self.tag_response().replace("refs/tags/v1.10.0", "refs/tags/v1.10.0-extra"),
                         'HTTP/2.0 200 OK\n\n{"ref":"refs/tags/v1.10.0","object":null}',
                         'HTTP/2.0 201 Created\n\n{}', 'not an HTTP response'):
            with self.subTest(response=response), patch.dict(os.environ, self.release_env(), clear=True), \
                    patch.object(bundle.subprocess, "check_output", side_effect=["[]", response]), \
                    patch.object(bundle.subprocess, "run") as run, self.assertRaises(ValueError):
                bundle.publish(self.root / "release", "1.10.0")
            run.assert_not_called()

    def test_manual_publication_fails_before_api_even_on_existing_tag(self):
        for tag in (False, True):
            env = dict(self.release_env(manual=not tag), GITHUB_EVENT_NAME="workflow_dispatch")
            with self.subTest(tag=tag), patch.dict(os.environ, env, clear=True), \
                    patch.object(bundle.subprocess, "check_output") as api, \
                    patch.object(bundle.subprocess, "run") as run, self.assertRaises(ValueError):
                bundle.publish(self.root / "release", "1.10.0")
            api.assert_not_called()
            run.assert_not_called()

    def test_mismatched_publication_version_fails_before_api(self):
        with patch.dict(os.environ, self.release_env(), clear=True), \
                patch.object(bundle.subprocess, "check_output") as api, \
                patch.object(bundle.subprocess, "run") as run, self.assertRaises(ValueError):
            bundle.publish(self.root / "release", "9.9.9")
        api.assert_not_called()
        run.assert_not_called()

    def test_changed_publication_checksums_block_publication(self):
        self.verify(self.fixtures())
        (self.root / "release/Omni-OS-Setup-1.10.0.exe").write_bytes(b"MZchanged")
        with patch.dict(os.environ, self.release_env(), clear=True), \
                patch.object(bundle.subprocess, "check_output") as api, \
                patch.object(bundle.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "checksums changed"):
            bundle.publish(self.root / "release", "1.10.0")
        api.assert_not_called()
        run.assert_not_called()

    def test_source_smoke_cannot_pass(self):
        # Smoke imports only the path helper before its frozen guard, not GUI/API.
        import bundle_smoke
        path = self.root / "source-report.json"
        self.assertEqual(bundle_smoke.run(["--smoke-test", "--smoke-report", str(path)]), 1)
        result = json.loads(path.read_text())
        self.assertFalse(result["ok"])
        self.assertIn("packaged executable", result["error"])

    def test_mac_boundary_is_whole_app_not_macos_folder(self):
        import bundle_smoke
        app = self.root / "Omni-OS.app"
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "executable", str(app / "Contents/MacOS/Omni-OS")):
            self.assertEqual(bundle_smoke.bundle_boundary(), app.resolve())


if __name__ == "__main__":
    unittest.main()
