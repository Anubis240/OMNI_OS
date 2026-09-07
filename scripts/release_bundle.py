"""Stdlib-only native packaging and fail-closed release gates (no app imports)."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile


TARGETS = ("windows-x64", "linux-x64", "macos-x64", "macos-arm64")
VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
CHECKS = {"assets", "imports", "qt_offscreen", "nudenet_inference",
          "browser_chromium", "browser_firefox", "browser_webkit"}
MAX_ASSET = 2 * 1024 ** 3
# Only the documented libc/desktop driver ABI stays on the OS. In particular,
# libstdc++, NSS/NSPR, GTK, WebKit, GLES/SwiftShader and xcb helpers are NOT exempt.
BASELINE = re.compile(
    r"(?:ld-linux-x86-64\.so\.2|lib(?:c|m|pthread|dl|rt|util|resolv|anl)\.so\.[0-9]+"
    r"|libnss_(?:files|dns|compat)\.so\.2"
    r"|lib(?:GL|GLX|GLdispatch|EGL|gbm|drm|drm_amdgpu|drm_intel|drm_radeon|asound|udev)\.so\.[0-9]+)"
)


def version_from_env(env):
    requested = requested_release_tag(env)
    if requested or env.get("GITHUB_REF_TYPE") == "tag":
        tag = requested or env.get("GITHUB_REF_NAME", "")
        if not re.fullmatch("v" + VERSION, tag):
            raise ValueError("Release tag must be exactly vX.Y.Z, without leading zeroes")
        return tag[1:]
    run = env.get("GITHUB_RUN_NUMBER", "0")
    if not re.fullmatch(r"0|[1-9][0-9]*", run):
        raise ValueError("Invalid run number")
    return "0.0." + run


def requested_release_tag(env):
    return env.get("REQUESTED_RELEASE_TAG", "") if env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" else ""


def release_version_from_env(env):
    if not requested_release_tag(env) and env.get("GITHUB_REF_TYPE") != "tag":
        raise ValueError("Release requires a tag or an explicit workflow_dispatch release_tag")
    return version_from_env(env)


def asset_names(target, version):
    if target not in TARGETS or not re.fullmatch(VERSION, version):
        raise ValueError("Invalid target/version")
    if target == "windows-x64":
        return [f"Omni-OS-Windows-x64-{version}.zip", f"Omni-OS-Setup-{version}.exe"]
    if target == "linux-x64":
        return [f"Omni-OS-Linux-x64-{version}.tar.gz"]
    return [f"Omni-OS-macOS-{target.split('-')[1]}-{version}.zip"]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def privacy(root):
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Missing bundle directory: {root}")
    roots = {(), ("_internal",), ("contents", "resources"),
             ("contents", "frameworks"), ("contents", "macos")}
    personal = {"api_keys.json", "apikeys.json", "memory.json", "trader.json",
                "wallet.json", "wallets.json", "credentials.json", "private_key.pem"}
    root_names = {"config.json", "settings.json", "voice.json", "theme.json", "seraph.log"}
    forbidden = []
    for path in root.rglob("*"):
        parts = tuple(p.lower() for p in path.relative_to(root).parts)
        if path.is_symlink() and (not path.exists() or not path.resolve().is_relative_to(root)):
            raise ValueError(f"Broken/external bundle symlink: {path}")
        if not path.is_file():
            continue
        name = parts[-1]
        bad = name == ".env" or name.startswith(".env.") or name in personal
        for base in roots:
            if parts[:len(base)] != base:
                continue
            local = parts[len(base):]
            bad |= bool(local) and (local[0] in {"config", "memory", "wallet", "wallets", "logs"}
                                    or (len(local) == 1 and name in root_names)
                                    or (local[0] == "trader" and path.suffix.lower() in {".json", ".log", ".db"}))
        if bad:
            forbidden.append(str(path.relative_to(root)))
    if forbidden:
        raise ValueError("Persisted application data: " + ", ".join(sorted(forbidden)))
    print(f"Privacy/provenance check passed: {root}")


def elf(path):
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) == b"\x7fELF"


def ldd_dependencies(path):
    result = subprocess.run(["ldd", str(path)], text=True, capture_output=True, timeout=30)
    text = result.stdout + result.stderr
    if "not found" in text:
        raise RuntimeError(f"Missing native dependency for {path}:\n{text}")
    if result.returncode and not any(s in text for s in ("not a dynamic executable", "statically linked")):
        raise RuntimeError(f"ldd failed for {path}:\n{text}")
    dependencies = []
    for line in text.splitlines():
        match = re.search(r"(?:=>\s*)?(/\S+)\s+\(", line)
        if match:
            dependency = Path(match[1])
            if not dependency.is_file():
                raise RuntimeError(f"ldd returned absent library: {dependency}")
            dependencies.append(dependency)
    return dependencies


def linux_binaries(roots):
    """Bounded ELF closure, retaining SONAMEs; ldd only trusted build inputs.

    Also collect known dlopen plugin roots, which ldd alone cannot discover.
    This is a build-time closure, NOT proof of completeness: the clean container
    must still exercise all three browsers and Qt/xcb before upload.
    """
    library_root = Path("/usr/lib/x86_64-linux-gnu")
    portaudio = library_root / "libportaudio.so.2"
    if not portaudio.is_file():
        raise RuntimeError("Build host must install libportaudio2")
    entries = [(portaudio, ".")]
    for directory, destination in (
        (library_root / "gstreamer-1.0", "gstreamer-1.0"),
        (library_root / "gio/modules", "gio/modules"),
        (library_root / "gstreamer1.0", "gst-helpers"),
        (Path("/usr/libexec/gstreamer-1.0"), "gst-helpers"),
    ):
        if directory.is_dir():
            entries.extend((p, str(Path(destination) / p.relative_to(directory).parent))
                           for p in directory.rglob("*") if elf(p))
    queue = [p for root in roots for p in root.rglob("*") if elf(p)]
    queue += [p for p, _ in entries]
    seen = set()
    collected = {}
    for source, destination in entries:
        collected[(destination, source.name)] = source
    while queue:
        path = queue.pop()
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if len(seen) > 12000:
            raise RuntimeError("ELF dependency traversal exceeded 12000 files")
        for dependency in ldd_dependencies(path):
            if BASELINE.fullmatch(dependency.name):
                continue
            key = (".", dependency.name)
            previous = collected.get(key)
            if previous and previous.resolve() != dependency.resolve() and digest(previous) != digest(dependency):
                raise RuntimeError(f"Conflicting SONAME: {previous}, {dependency}")
            collected[key] = dependency
            queue.append(dependency)
    print(f"Linux closure: {len(seen)} ELF inputs, {len(collected)} collected libraries/plugins")
    return [(str(source), destination) for (destination, _), source in sorted(collected.items())]


def archive_members_safe(path):
    """Reject traversal, escaping links and special files before extraction."""
    members = {}
    links = {}

    def record(name, directory=False, link=None, hardlink=False):
        check(name, link, hardlink)
        key = PurePosixPath(name).parts
        if not key or key in members:
            raise ValueError(f"Empty/duplicate archive member: {name}")
        members[key] = "link" if link is not None else "directory" if directory else "file"
        if link is not None:
            links[key] = (link, hardlink)

    def resolve(parts, parent=(), active=frozenset(), budget=None):
        # Do not normpath targets: a preceding component may itself be a link.
        if budget is None:
            budget = [256]
        resolved = parent
        for part in parts:
            if part in {"", "."}:
                continue
            if part == "..":
                if not resolved:
                    raise ValueError("Archive link graph escapes extraction root")
                resolved = resolved[:-1]
                continue
            key = (*resolved, part)
            if key in links:
                budget[0] -= 1
                if key in active or len(active) >= 40 or budget[0] < 0:
                    raise ValueError("Cyclic/excessively deep archive link graph")
                target, hardlink = links[key]
                resolved = resolve(target.split("/"), () if hardlink else resolved, active | {key}, budget)
            else:
                resolved = key
        return resolved

    def check(name, link=None, hardlink=False):
        if "\0" in name or "\\" in name or ":" in name or name.startswith("/") or ".." in PurePosixPath(name).parts:
            raise ValueError(f"Unsafe archive member: {name}")
        if link is not None:
            target = posixpath.normpath(link if hardlink else posixpath.join(posixpath.dirname(name), link))
            if "\0" in link or "\\" in link or ":" in link or link.startswith("/") or target == ".." or target.startswith("../"):
                raise ValueError(f"Escaping archive link: {name}")
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                # filename may already have lost NUL suffixes or Windows
                # backslashes; validate the original central-directory name.
                check(entry.orig_filename)
                mode = entry.external_attr >> 16
                link = archive.read(entry).decode("utf-8") if stat.S_ISLNK(mode) else None
                record(entry.orig_filename, entry.is_dir(), link)
    else:
        with tarfile.open(path, "r:gz") as archive:
            for entry in archive:
                if not (entry.isfile() or entry.isdir() or entry.issym() or entry.islnk()):
                    raise ValueError(f"Special archive member: {entry.name}")
                record(entry.name, entry.isdir(), entry.linkname if entry.issym() or entry.islnk() else None, entry.islnk())

    # Inspect the complete namespace, regardless of archive entry order. Never
    # let an extractor write through a link or replace a non-directory ancestor.
    for key in members:
        for length in range(1, len(key)):
            ancestor = key[:length]
            if ancestor in members and members[ancestor] != "directory":
                raise ValueError(f"Non-directory archive ancestor: {'/'.join(ancestor)}")
        resolve(key)


def verify_report(path, qt=None):
    result = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(result, indent=2))
    if result.get("ok") is not True or set(result.get("checks", [])) != CHECKS:
        raise ValueError("Smoke failed or did not run every required check")
    if qt and result.get("qt_platform") != qt:
        raise ValueError(f"Smoke did not use Qt {qt}")


def clean_environment(home):
    # Allowlist, not a copy of builder env (no venv, PYTHONPATH, Homebrew, caches,
    # API tokens, LD_LIBRARY_PATH, or inherited PLAYWRIGHT_BROWSERS_PATH).
    keys = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "DISPLAY", "XAUTHORITY")
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), TMP=str(home), TEMP=str(home),
               TMPDIR=str(home), XDG_CONFIG_HOME=str(home / "config"),
               XDG_CACHE_HOME=str(home / "cache"), XDG_DATA_HOME=str(home / "data"),
               APPDATA=str(home / "AppData/Roaming"), LOCALAPPDATA=str(home / "AppData/Local"))
    env["PATH"] = (str(Path(os.environ["SYSTEMROOT"]) / "System32") + os.pathsep + os.environ["SYSTEMROOT"]
                   if sys.platform == "win32" else "/usr/bin:/bin:/usr/sbin:/sbin")
    return env


def smoke_extracted(root, report, qt="offscreen"):
    privacy(root)
    executable = (root / "Contents/MacOS/Omni-OS" if root.suffix == ".app"
                  else root / ("Omni-OS.exe" if sys.platform == "win32" else "Omni-OS"))
    if root.suffix == ".app":
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(root)], check=True)
    with tempfile.TemporaryDirectory(prefix="omni-clean-home-") as home:
        env = clean_environment(Path(home))
        env["QT_QPA_PLATFORM"] = qt
        process = subprocess.Popen([str(executable.resolve()), "--smoke-test", "--smoke-report", str(report.resolve())],
                                   cwd=home, env=env, start_new_session=sys.platform != "win32")
        try:
            code = process.wait(timeout=300)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=True)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        verify_report(report, qt)
        if code:
            raise RuntimeError(f"Frozen executable exited {code}")


def package(target, version, output):
    native = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}[platform.system()]
    arch = {"AMD64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine()]
    if target != f"{native}-{arch}":
        raise ValueError("Cross compilation is forbidden; use the matching native runner")
    names = asset_names(target, version)
    root = Path("dist/Omni-OS.app" if native == "macos" else "dist/Omni-OS")
    privacy(root)
    output.mkdir(parents=True, exist_ok=False)
    archive = output / names[0]
    if native == "macos":
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(root)], check=True)
        subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(root), str(archive)], check=True)
    elif native == "linux":
        with tarfile.open(archive, "w:gz", dereference=False) as stream:
            stream.add(root, arcname=root.name)
    else:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as stream:
            for path in sorted(root.rglob("*")):
                stream.write(path, path.relative_to(root.parent))
        shutil.copy2(Path("installer/output") / names[1], output / names[1])
    archive_members_safe(archive)
    # Every smoke runs from a NEW extraction of the final distributed archive.
    with tempfile.TemporaryDirectory(prefix="omni-final-package-") as temporary:
        temporary = Path(temporary)
        if native == "macos":
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(temporary)], check=True)
        elif native == "linux":
            with tarfile.open(archive, "r:gz") as stream:
                stream.extractall(temporary, filter="data")
        else:
            with zipfile.ZipFile(archive) as stream:
                stream.extractall(temporary)
        report = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / f"smoke-{target}.json"
        report.unlink(missing_ok=True)
        smoke_extracted(temporary / root.name, report)
        if native == "windows":
            # Exercise the final installer silently as well; suppress postinstall
            # app startup, and smoke the independently installed folder.
            installed = temporary / "installed"
            subprocess.run([str((output / names[1]).resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                            "/SP-", f"/DIR={installed}"], check=True, timeout=300)
            installer_report = report.with_name("smoke-windows-installer.json")
            installer_report.unlink(missing_ok=True)
            smoke_extracted(installed, installer_report)
    manifest = {"target": target, "version": version, "commit": os.environ["GITHUB_SHA"],
                "run_id": os.environ["GITHUB_RUN_ID"], "assets": {}}
    for name in names:
        path = output / name
        if not 0 < path.stat().st_size < MAX_ASSET:
            raise ValueError(f"Asset must be nonempty and below 2 GiB: {name}")
        manifest["assets"][name] = {"sha256": digest(path), "size": path.stat().st_size}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def verify_artifacts(downloads, output, version, commit, run_id):
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9]+", run_id):
        raise ValueError("Invalid commit/run identity")
    expected_dirs = {f"bundle-{run_id}-{target}" for target in TARGETS}
    if {p.name for p in downloads.iterdir()} != expected_dirs:
        raise ValueError("Expected exactly four target artifacts from this run")
    verified = []
    for target in TARGETS:
        directory = downloads / f"bundle-{run_id}-{target}"
        names = asset_names(target, version)
        if directory.is_symlink() or {p.name for p in directory.iterdir()} != set(names) | {"manifest.json"}:
            raise ValueError(f"Unexpected artifact file set: {target}")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if set(manifest) != {"target", "version", "commit", "run_id", "assets"}:
            raise ValueError(f"Invalid manifest schema: {target}")
        identity = {k: manifest.get(k) for k in ("target", "version", "commit", "run_id")}
        if identity != dict(target=target, version=version, commit=commit, run_id=run_id):
            raise ValueError(f"Artifact identity mismatch: {target}")
        if set(manifest.get("assets", {})) != set(names):
            raise ValueError(f"Manifest asset set mismatch: {target}")
        for name in names:
            path = directory / name
            record = manifest["assets"][name]
            if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size < MAX_ASSET:
                raise ValueError(f"Invalid asset: {name}")
            if record != {"sha256": digest(path), "size": path.stat().st_size}:
                raise ValueError(f"Checksum/size mismatch: {name}")
            if name.endswith(".exe"):
                with path.open("rb") as stream:
                    if stream.read(2) != b"MZ":
                        raise ValueError("Installer is not a PE executable")
            else:
                archive_members_safe(path)
            verified.append(path)
    output.mkdir(parents=True, exist_ok=False)
    for path in verified:
        shutil.copy2(path, output / path.name)
    sums = "".join(f"{digest(path)}  {path.name}\n" for path in sorted(output.iterdir(), key=lambda path: path.name))
    (output / "SHA256SUMS").write_text(sums, encoding="ascii")
    print("Verified four native manifests, five binary assets and aggregate SHA256SUMS")


def remote_tag_reference(tag):
    # Parse the actual HTTP status, never an error-message substring. Only an
    # explicit 404 permits creation; auth, transport and malformed replies fail.
    command = ["gh", "api", "--include", f"repos/{{owner}}/{{repo}}/git/ref/tags/{tag}"]
    try:
        response = subprocess.check_output(command, text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        if re.match(r"HTTP/\S+ 404(?:[ \r\n])", error.output or ""):
            return None
        raise
    headers, body = re.split(r"\r?\n\r?\n", response, maxsplit=1)
    if not re.match(r"HTTP/\S+ 200(?:[ \r\n])", headers):
        raise ValueError("Unexpected tag lookup HTTP status")
    result = json.loads(body)
    if result["ref"] != "refs/tags/" + tag or not isinstance(result["object"], dict):
        raise ValueError("Remote tag reference mismatch")
    return result["object"]


def publish(output, version):
    if release_version_from_env(os.environ) != version:
        raise ValueError("Publication version differs from authorized release tag")
    names = {name for target in TARGETS for name in asset_names(target, version)}
    if {p.name for p in output.iterdir()} != names | {"SHA256SUMS"}:
        raise ValueError("Publish requires exactly five binaries and SHA256SUMS")
    sums = ""
    for name in sorted(names):
        path = output / name
        if path.is_symlink() or not 0 < path.stat().st_size < MAX_ASSET:
            raise ValueError(f"Invalid publication asset: {name}")
        sums += f"{digest(path)}  {name}\n"
    if (output / "SHA256SUMS").read_text(encoding="ascii") != sums:
        raise ValueError("Publication checksums changed after verification")
    tag = "v" + version
    # Distinguish 'absent' from API/auth errors. Listing must itself succeed.
    releases = json.loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp",
                          "repos/{owner}/{repo}/releases?per_page=100"], text=True))
    if any(item["tag_name"] == tag for page in releases for item in page):
        raise ValueError("Release/draft already exists; manual recovery required, never overwrite")
    # --verify-tag alone only checks existence; a moved tag must not publish
    # artifacts from a different commit. Peel annotated tags, bounded to 5 levels.
    reference = remote_tag_reference(tag)
    if reference is None:
        if requested_release_tag(os.environ) != tag:
            raise ValueError("Triggered release tag is missing; never recreate it")
        commit = os.environ["GITHUB_SHA"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Invalid release commit")
        # Create only, never PATCH/force or retry a conflicting writer. A tag
        # created with GITHUB_TOKEN does not trigger another build workflow.
        subprocess.run(["gh", "api", "--method", "POST", "repos/{owner}/{repo}/git/refs",
                        "-f", f"ref=refs/tags/{tag}", "-f", f"sha={commit}"], check=True)
        reference = remote_tag_reference(tag)
        if reference is None:
            raise ValueError("Created tag is missing; manual recovery required")
    for _ in range(5):
        if reference["type"] != "tag":
            break
        sha = reference["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid annotated tag object")
        reference = json.loads(subprocess.check_output(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/git/tags/{sha}"], text=True))["object"]
    if reference != {"type": "commit", "sha": os.environ["GITHUB_SHA"], "url": reference.get("url")}:
        raise ValueError("Remote tag no longer points to this workflow's commit")
    assets = sorted(output.iterdir())
    subprocess.run(["gh", "release", "create", tag, "--verify-tag", "--target", os.environ["GITHUB_SHA"],
                    "--generate-notes", "--draft", "--title", f"Omni-OS {version}",
                    *map(str, assets)], check=True)
    # Re-download all draft assets and verify their bytes before publication.
    with tempfile.TemporaryDirectory(prefix="omni-draft-verify-") as temporary:
        subprocess.run(["gh", "release", "download", tag, "--dir", temporary], check=True)
        downloaded = Path(temporary)
        if {p.name for p in downloaded.iterdir()} != {p.name for p in assets}:
            raise ValueError("Incomplete draft uploads; manual recovery required")
        for path in assets:
            if digest(path) != digest(downloaded / path.name):
                raise ValueError("Draft checksum mismatch; manual recovery required")
    subprocess.run(["gh", "release", "edit", tag, "--draft=false"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    sub.add_parser("syntax")
    build = sub.add_parser("package")
    build.add_argument("--target", choices=TARGETS, required=True)
    build.add_argument("--output", type=Path, default=Path("release-assets"))
    report = sub.add_parser("report")
    report.add_argument("path", type=Path)
    report.add_argument("--qt", choices=("offscreen", "xcb"))
    validate = sub.add_parser("verify")
    validate.add_argument("--downloads", type=Path, default=Path("downloaded"))
    validate.add_argument("--output", type=Path, default=Path("release-assets"))
    sub.add_parser("publish")
    args = parser.parse_args()
    if args.command == "version":
        version = version_from_env(os.environ)
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write(f"APP_VERSION={version}\n")
        print(f"Validated version: {version}")
    elif args.command == "syntax":
        files = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]).decode().split("\0")
        for name in set(files):
            if name.endswith((".py", ".spec")) and Path(name).is_file():
                compile(Path(name).read_bytes(), name, "exec")
        print("Python/spec syntax compilation passed (no imports)")
    elif args.command == "package":
        package(args.target, os.environ["APP_VERSION"], args.output)
    elif args.command == "report":
        verify_report(args.path, args.qt)
    elif args.command == "verify":
        verify_artifacts(args.downloads, args.output, release_version_from_env(os.environ),
                         os.environ["GITHUB_SHA"], os.environ["GITHUB_RUN_ID"])
    elif args.command == "publish":
        publish(Path("release-assets"), release_version_from_env(os.environ))


if __name__ == "__main__":
    main()
