"""Stdlib-only native packaging and fail-closed release gates (no app imports)."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
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
    if env.get("GITHUB_REF_TYPE") == "tag":
        tag = env.get("GITHUB_REF_NAME", "")
        if not re.fullmatch("v" + VERSION, tag):
            raise ValueError("Release tag must be exactly vX.Y.Z, without leading zeroes")
        return tag[1:]
    run = env.get("GITHUB_RUN_NUMBER", "0")
    if not re.fullmatch(r"0|[1-9][0-9]*", run):
        raise ValueError("Invalid run number")
    return "0.0." + run


def release_version_from_env(env):
    if (env.get("GITHUB_EVENT_NAME") != "push" or env.get("GITHUB_REF_TYPE") != "tag"
            or not env.get("GITHUB_REF", "").startswith("refs/tags/v")
            or env["GITHUB_REF"] != "refs/tags/" + env.get("GITHUB_REF_NAME", "")):
        raise ValueError("Release requires a push of an exact vX.Y.Z tag")
    return version_from_env(env)


def asset_names(target, version):
    if target not in TARGETS or not re.fullmatch(VERSION, version):
        raise ValueError("Invalid target/version")
    if target == "windows-x64":
        return [f"Omni-OS-Windows-x64-{version}.zip", f"Omni-OS-Setup-{version}.exe"]
    if target == "linux-x64":
        return [f"Omni-OS-Linux-x64-{version}.tar.gz"]
    return [f"Omni-OS-macOS-{target.split('-')[1]}-{version}.dmg"]


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


def ldd_dependencies(path, library_paths=()):
    # Never inherit a builder-wide search path into a distribution's loader.
    # This environment belongs only to ldd, not the frozen runtime.
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    if library_paths:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(map(str, library_paths))
    result = subprocess.run(["ldd", str(path)], text=True, capture_output=True, timeout=30, env=env)
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


def linux_elf_inputs(roots):
    """Inspect selected files or complete trusted distributions, never siblings."""
    return [p for root in roots for p in (root.rglob("*") if root.is_dir() else (root,)) if elf(p)]


def linux_loader_paths(path, browser_root=None, library_paths=(), library_scope=None):
    """Nearest directory first, then ancestors; never sibling engines/plugins.

    A nested Firefox GMP needs the Firefox root, whereas WebKit's GTK and WPE
    wrappers each set lib:sys/lib within their own minibrowser. Otherwise only
    ancestor lib directories join the search. Qt paths apply only to Qt inputs.
    """
    path = path.resolve()
    if browser_root is not None:
        browser_root = browser_root.resolve()
        if path.is_relative_to(browser_root):
            distribution = browser_root / path.relative_to(browser_root).parts[0]
            relative = path.relative_to(distribution)
            if (distribution.name.startswith("webkit-") and len(relative.parts) > 1
                    and relative.parts[0] in {"minibrowser-gtk", "minibrowser-wpe"}):
                minibrowser = distribution / relative.parts[0]
                return [minibrowser / "lib", minibrowser / "sys/lib"]
            paths = []
            parent = path.parent
            while parent.is_relative_to(distribution):
                paths.append(parent)
                if (parent / "lib").is_dir():
                    paths.append(parent / "lib")
                if parent == distribution:
                    break
                parent = parent.parent
            return paths
    if library_scope is not None and path.is_relative_to(library_scope.resolve()):
        return list(library_paths)
    return []


def linux_runtime_destination(path, browser_root=None):
    """WebKit wrappers replace LD_LIBRARY_PATH with their own lib:sys/lib."""
    if browser_root is not None:
        path = path.resolve()
        browser_root = browser_root.resolve()
        if path.is_relative_to(browser_root):
            parts = path.relative_to(browser_root).parts
            if (len(parts) > 2 and parts[0].startswith("webkit-")
                    and parts[1] in {"minibrowser-gtk", "minibrowser-wpe"}):
                return str(Path("playwright/driver/package/.local-browsers")
                           / parts[0] / parts[1] / "sys/lib")
    return "."


def linux_binaries(roots, library_paths=(), *, library_scope=None, browser_root=None):
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
    gles = library_root / "libGLESv2.so.2"
    if not gles.is_file():
        raise RuntimeError("Build host must install libgles2")
    entries.append((gles, "."))
    for directory, destination in (
        (library_root / "gstreamer-1.0", "gstreamer-1.0"),
        (library_root / "gio/modules", "gio/modules"),
        (library_root / "gstreamer1.0", "gst-helpers"),
        (Path("/usr/libexec/gstreamer-1.0"), "gst-helpers"),
    ):
        if directory.is_dir():
            entries.extend((p, str(Path(destination) / p.relative_to(directory).parent))
                           for p in directory.rglob("*") if elf(p))
    # File roots allow the Qt hooks to select the actual modules/plugins rather
    # than scanning unused QML/designer plugins throughout the installed wheel.
    inputs = linux_elf_inputs(roots) + [p for p, _ in entries]
    queue = [(p, linux_loader_paths(p, browser_root, library_paths, library_scope),
              linux_runtime_destination(p, browser_root)) for p in inputs]
    # The runtime hook exposes GIO/GStreamer plugins globally, but WebKit's
    # wrapper also hides their external dependencies. Traverse dlopen roots in
    # each wrapper's loader context, not just the builder/global context.
    webkit_contexts = {(tuple(search), destination) for _, search, destination in queue
                       if destination != "."}
    dlopen_roots = [source for source, destination in entries if destination != "." or source == gles]
    for search, destination in webkit_contexts:
        # dlopen's entry library itself is not an ldd dependency. Plugins stay
        # at GIO_MODULE_DIR/GST_PLUGIN_PATH; GLES needs a wrapper-local copy.
        entries.append((gles, destination))
        queue.extend((source, list(search), destination) for source in dlopen_roots)
    seen = set()
    collected = {}
    for source, destination in entries:
        collected[(destination, source.name)] = source
    while queue:
        path, search, runtime_destination = queue.pop()
        resolved = path.resolve()
        context = (resolved, tuple(search), runtime_destination)
        if context in seen:
            continue
        seen.add(context)
        if len(seen) > 12000:
            raise RuntimeError("ELF dependency traversal exceeded 12000 files")
        for dependency in ldd_dependencies(path, search):
            if BASELINE.fullmatch(dependency.name):
                continue
            destination = runtime_destination
            if browser_root is not None and dependency.resolve().is_relative_to(browser_root.resolve()):
                relative = dependency.relative_to(browser_root)
                destination = str(Path("playwright/driver/package/.local-browsers") / relative.parent)
            key = (destination, dependency.name)
            previous = collected.get(key)
            if previous and previous.resolve() != dependency.resolve() and digest(previous) != digest(dependency):
                raise RuntimeError(f"Conflicting SONAME: {previous}, {dependency}")
            collected[key] = dependency
            # Inspect transitive dependencies in the same ELF load context.
            queue.append((dependency, search, runtime_destination))
    print(f"Linux closure: {len(seen)} ELF inputs, {len(collected)} collected libraries/plugins")
    return [(str(source), destination) for (destination, _), source in sorted(collected.items())]


def without_browser_toc(entries, browser_root):
    """Leave self-contained browsers out of COLLECT/BUNDLE Mach-O rewriting."""
    browser_root = browser_root.resolve()
    prefix = "playwright/driver/package/.local-browsers"

    def browser_destination(name):
        name = name.replace("\\", "/")
        return name == prefix or name.startswith(prefix + "/")

    return [(destination, source, kind) for destination, source, kind in entries
            if not (browser_destination(destination)
                    or Path(source).resolve().is_relative_to(browser_root)
                    # Analysis also adds top-level aliases to browser dylibs.
                    # These would be dangling while BUNDLE is signing itself.
                    or (kind == "SYMLINK" and browser_destination(posixpath.normpath(posixpath.join(
                        posixpath.dirname(destination.replace("\\", "/")), source.replace("\\", "/"))))))]


def browser_tree_manifest(root, *, all_modes=False):
    """Compare bytes, executable modes and link targets without following links."""
    root = root.resolve()
    manifest = {}
    if all_modes:
        manifest["."] = ("directory", stat.S_IMODE(root.stat().st_mode))
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            if not path.exists() or not path.resolve().is_relative_to(root):
                raise ValueError(f"Broken/external browser symlink: {path}")
            manifest[name] = ("link", os.readlink(path))
        elif path.is_file():
            manifest[name] = ("file", stat.S_IMODE(path.stat().st_mode), digest(path))
        elif path.is_dir():
            manifest[name] = ("directory",)
        else:
            raise ValueError(f"Special browser file: {path}")
        if all_modes and manifest[name][0] != "file":
            manifest[name] += (stat.S_IMODE(path.lstat().st_mode),)
    return manifest


def append_linux_browsers(root, browser_root, dependencies=()):
    """Preserve complete upstream trees, including ELF loaded only via dlopen.

    Analysis can omit shared-library data or replace it with global SONAME
    aliases. A dependency closure alone cannot reconstruct those payloads.
    External dependencies still pass linux_binaries and the clean smoke gate.
    """
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Browser assembly requires a native Linux runner")
    browser_root = browser_root.resolve()
    for engine in ("chromium", "firefox", "webkit"):
        if not any(p.is_dir() for p in browser_root.glob(f"{engine}-*")):
            raise ValueError(f"Missing bundled {engine}")
    original = browser_tree_manifest(browser_root)
    destination = root.resolve() / "playwright/driver/package/.local-browsers"
    if not destination.parent.is_dir():
        raise ValueError("COLLECT is missing the Playwright driver package")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Browsers must be excluded from COLLECT before copying")
    shutil.copytree(browser_root, destination, symlinks=True)
    if browser_tree_manifest(destination) != original:
        raise ValueError("Browser copy changed upstream bytes, modes or links")
    print("Linux browsers preserved byte-for-byte, including dlopen libraries")
    # These entries are deliberately excluded from COLLECT along with browsers.
    # Add external closure libraries only AFTER verifying the upstream copy;
    # never replace upstream private libraries or create global aliases to them.
    prefix = Path("playwright/driver/package/.local-browsers")
    for source, directory in dependencies:
        directory = Path(directory)
        if not directory.is_relative_to(prefix):
            continue
        source = Path(source)
        if source.resolve().is_relative_to(browser_root):
            continue
        target = root.resolve() / directory / source.name
        if target.exists() or target.is_symlink():
            if not target.is_file() or digest(target) != digest(source):
                raise ValueError(f"External dependency conflicts with browser library: {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def append_macos_browsers(app, browser_root):
    """Copy upstream distributions unchanged, then seal ONLY the outer wrapper.

    PyInstaller rewrites Mach-O load commands and re-signs collected binaries.
    Firefox's liblgpllibs lacks header padding for that extra LC_RPATH. These
    distributions already contain their own loader paths and signatures; they
    must not pass through process_collected_binary or recursive re-signing.
    """
    if sys.platform != "darwin":
        raise RuntimeError("Browser app assembly requires a native macOS runner")
    app = app.resolve()
    browser_root = browser_root.resolve()
    for engine in ("chromium", "firefox", "webkit"):
        if not any(p.is_dir() for p in browser_root.glob(f"{engine}-*")):
            raise ValueError(f"Missing bundled {engine}")
    original = browser_tree_manifest(browser_root)
    relative = Path("playwright/driver/package/.local-browsers")
    destination = app / "Contents/Resources" / relative
    alias = app / "Contents/Frameworks" / relative
    if not destination.parent.is_dir() or not alias.parent.is_dir():
        raise ValueError("BUNDLE is missing the Playwright driver package")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Browsers must be excluded from BUNDLE before copying")
    shutil.copytree(browser_root, destination, symlinks=True)
    # Support both JS realpaths (Resources) and Python _MEIPASS (Frameworks).
    if alias.resolve() != destination.resolve():
        alias.symlink_to(os.path.relpath(destination, alias.parent), target_is_directory=True)
    if browser_tree_manifest(destination) != original:
        raise ValueError("Browser copy changed upstream bytes, modes or links")
    # No --deep signing: upstream nested signatures/entitlements stay intact.
    # Replace the stale wrapper signature explicitly, without --force.
    subprocess.run(["/usr/bin/codesign", "--remove-signature", str(app)], check=True)
    subprocess.run(["/usr/bin/codesign", "--sign", "-", str(app)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app)], check=True)
    if browser_tree_manifest(destination) != original:
        raise ValueError("Wrapper signing modified an upstream browser")
    print("macOS browsers preserved byte-for-byte; final wrapper signature verified")


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


def manifest_difference(before, after):
    """Diagnostic only: equality does NOT prove equal xattrs/resource forks/ACLs."""
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed = {name: {"before": before[name], "after": after[name]}
               for name in sorted(before.keys() & after.keys()) if before[name] != after[name]}
    return {"counts": {"added": len(added), "removed": len(removed), "changed": len(changed)},
            "added": added, "removed": removed, "changed": changed}


@contextmanager
def macos_package_report(target):
    path = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / f"smoke-package-{target}.json"
    report = {"target": target, "stage": "pre-image-verify", "errors": [], "native_verify": {},
              "native_commands": [], "retained_paths": [],
              "manifests": {}, "diff": None,
              "limitations": "Manifest equality does not prove equal xattrs, resource forks or ACLs; "
                             "only file hashes, relative paths, types, link targets and modes are compared."}
    failed = False
    try:
        yield report
    except BaseException as error:
        failed = True
        report["errors"].append(native_error_record(error, report["stage"]))
        raise
    finally:
        # Never replace a gate's original exception with a report-writing error.
        try:
            path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            if not failed:
                raise
            print(f"Could not persist macOS package diagnostics: {error}", file=sys.stderr)


def verify_macos_signature(root, diagnostics=None):
    command = ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=4", str(root)]
    if diagnostics is None:
        subprocess.run(command, check=True, timeout=120)
        return
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        diagnostics["native_verify"][diagnostics["stage"]] = native_error_record(error, diagnostics["stage"])
        raise
    else:
        diagnostics["native_verify"][diagnostics["stage"]] = {
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def native_error_record(error, stage):
    record = {"stage": stage, "type": type(error).__name__, "message": str(error)}
    if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        record["command"] = error.cmd
        record["returncode"] = getattr(error, "returncode", None)
        for name in ("stdout", "stderr"):
            value = getattr(error, name, None)
            record[name] = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return record


def run_hdiutil(arguments, diagnostics, *, timeout=120):
    command = ["/usr/bin/hdiutil", *map(str, arguments)]
    record = {"stage": diagnostics["stage"], "command": command}
    diagnostics["native_commands"].append(record)
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        record.update(native_error_record(error, diagnostics["stage"]))
        raise
    record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    return result


@contextmanager
def mounted_macos_image(image, mountpoint, diagnostics, *, readonly=False):
    """Own just this mountpoint; even an interrupted/partial attach gets rollback.

    If detach cannot confirm success, retain the workspace. Never recursively
    delete a possibly mounted filesystem (including through temp finalizers).
    """
    mountpoint.mkdir()
    failed = False
    try:
        result = run_hdiutil(["attach", image, "-plist", "-nobrowse", "-mountpoint", mountpoint,
                              *(["-readonly"] if readonly else [])], diagnostics)
        entities = plistlib.loads(result.stdout.encode("utf-8"))["system-entities"]
        if not any(entity.get("mount-point") == str(mountpoint) for entity in entities):
            raise ValueError("hdiutil did not confirm the requested mountpoint")
        yield mountpoint
    except BaseException:
        failed = True
        raise
    finally:
        stage = diagnostics["stage"]
        diagnostics["stage"] = "detach-readonly" if readonly else "detach-writable"
        try:
            run_hdiutil(["detach", mountpoint], diagnostics)
        except BaseException as error:
            diagnostics["retained_paths"].append(str(mountpoint.parent))
            diagnostics["errors"].append(native_error_record(error, diagnostics["stage"]))
            if not failed:
                stage = diagnostics["stage"]
                raise
        finally:
            diagnostics["stage"] = stage


def macos_image_size_mib(root):
    # Reserve at least each file's logical, block-rounded size: sparse/compressed
    # source extents may expand on copy. Include directory/link allocation too.
    allocated = 0
    for path in (root, *root.rglob("*")):
        info = path.lstat()
        allocated += max(getattr(info, "st_blocks", 0) * 512,
                         ((info.st_size + 4095) // 4096) * 4096)
    mib = 1024 ** 2
    return (allocated * 6 + 5 * mib - 1) // (5 * mib) + 256


def verify_dmg(path):
    """Portable format gate only; native mounting/smoke happens on the builder."""
    if path.suffix != ".dmg" or path.stat().st_size < 512:
        raise ValueError("Invalid DMG extension/size")
    with path.open("rb") as stream:
        stream.seek(-512, os.SEEK_END)
        if stream.read(4) != b"koly":
            raise ValueError("DMG is missing its UDIF trailer")


def compare_macos_image(root, before, diagnostics, key):
    after = browser_tree_manifest(root, all_modes=True)
    diagnostics["manifests"][key] = after
    difference = manifest_difference(before, after)
    diagnostics["diff" if key == "after" else "copy_diff"] = difference
    if before != after:
        raise ValueError("macOS image changed app bytes, paths, types, modes or links")


def package_macos(root, archive, target):
    if sys.platform != "darwin":
        raise RuntimeError("macOS package diagnostics require a native macOS runner")
    with macos_package_report(target) as diagnostics:
        # mkdtemp has no recursive finalizer: failed detaches must leave it alone.
        temporary = Path(tempfile.mkdtemp(prefix="omni-final-package-")).resolve()
        diagnostics["workspace"] = str(temporary)
        failed = False
        try:
            root = root.resolve()
            archive = archive.resolve()
            if archive.suffix != ".dmg":
                raise ValueError("macOS packages require .dmg")
            verify_macos_signature(root, diagnostics)
            diagnostics["stage"] = "pre-image-manifest"
            # Freeze before any copy; never rebaseline from a mutated source.
            before = browser_tree_manifest(root, all_modes=True)
            diagnostics["manifests"]["before"] = before
            diagnostics["stage"] = "create-image"
            image = temporary / "transport.sparseimage"
            run_hdiutil(["create", image, "-size", f"{macos_image_size_mib(root)}m",
                         "-fs", "APFS", "-volname", "Omni-OS", "-type", "SPARSE"], diagnostics, timeout=300)
            diagnostics["stage"] = "attach-writable"
            with mounted_macos_image(image, temporary / "writable", diagnostics) as mounted:
                diagnostics["stage"] = "copy-image"
                copied = mounted / root.name
                # No ditto/-srcfolder: ._ files are literal signed resources,
                # even when their contents look exactly like AppleDouble.
                shutil.copytree(root, copied, symlinks=True)
                diagnostics["stage"] = "copy-image-manifest"
                compare_macos_image(copied, before, diagnostics, "copied")
                diagnostics["stage"] = "copy-image-verify"
                verify_macos_signature(copied, diagnostics)
            diagnostics["stage"] = "convert-image"
            run_hdiutil(["convert", image, "-format", "UDZO", "-o", archive], diagnostics, timeout=600)
            diagnostics["stage"] = "image-format"
            verify_dmg(archive)
            diagnostics["stage"] = "attach-readonly"
            with mounted_macos_image(archive, temporary / "readonly", diagnostics, readonly=True) as mounted:
                extracted = mounted / root.name
                diagnostics["stage"] = "post-image-privacy"
                privacy(extracted)
                diagnostics["stage"] = "post-image-manifest"
                compare_macos_image(extracted, before, diagnostics, "after")
                report = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / f"smoke-{target}.json"
                report.unlink(missing_ok=True)
                smoke_extracted(extracted, report, diagnostics=diagnostics)
            diagnostics["stage"] = "complete"
        except BaseException:
            failed = True
            raise
        finally:
            if not diagnostics["retained_paths"]:
                try:
                    shutil.rmtree(temporary)
                except OSError as error:
                    diagnostics["retained_paths"].append(str(temporary))
                    diagnostics["errors"].append(native_error_record(error, "cleanup"))
                    if not failed:
                        diagnostics["stage"] = "cleanup"
                        raise


def smoke_extracted(root, report, qt="offscreen", *, diagnostics=None):
    privacy(root)
    executable = (root / "Contents/MacOS/Omni-OS" if root.suffix == ".app"
                  else root / ("Omni-OS.exe" if sys.platform == "win32" else "Omni-OS"))
    if root.suffix == ".app":
        if diagnostics is not None:
            diagnostics["stage"] = "post-image-verify"
        verify_macos_signature(root, diagnostics)
    if diagnostics is not None:
        diagnostics["stage"] = "smoke"
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
        package_macos(root, archive, target)
    elif native == "linux":
        with tarfile.open(archive, "w:gz", dereference=False) as stream:
            stream.add(root, arcname=root.name)
    else:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as stream:
            for path in sorted(root.rglob("*")):
                stream.write(path, path.relative_to(root.parent))
        shutil.copy2(Path("installer/output") / names[1], output / names[1])
    if native != "macos":
        smoke_archive(root, archive, target, native, output, names)
    manifest = {"target": target, "version": version, "commit": os.environ["GITHUB_SHA"],
                "run_id": os.environ["GITHUB_RUN_ID"], "assets": {}}
    for name in names:
        path = output / name
        if not 0 < path.stat().st_size < MAX_ASSET:
            raise ValueError(f"Asset must be nonempty and below 2 GiB: {name}")
        manifest["assets"][name] = {"sha256": digest(path), "size": path.stat().st_size}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def smoke_archive(root, archive, target, native, output, names):
    archive_members_safe(archive)
    # Every smoke runs from a NEW extraction of the final distributed archive.
    with tempfile.TemporaryDirectory(prefix="omni-final-package-") as temporary:
        temporary = Path(temporary)
        if native == "linux":
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
            elif name.endswith(".dmg"):
                verify_dmg(path)
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
    # explicit 404 means absent; auth, transport and malformed replies fail.
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
        raise ValueError("Triggered release tag is missing; never recreate it")
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
