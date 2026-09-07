"""Regression for Qt/xcb dependencies absent from a clean Jammy builder."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LinuxBuildDependenciesTests(unittest.TestCase):
    def test_ci_installs_xcb_dependencies_before_analysis(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        linux_step = workflow.split("- name: Linux build-only system dependencies", 1)[1]
        linux_step = linux_step.split("- name:", 1)[0]
        install = linux_step.split("sudo apt-get install", 1)[1]
        install = install.split('"$BUILD_PYTHON"', 1)[0]
        for package in ("libportaudio2", "libxcb-cursor0", "libxcb-icccm4",
                        "libxcb-keysyms1", "libxcb-xkb1", "libxkbcommon-x11-0"):
            with self.subTest(package=package):
                self.assertIn(package, install.split())

    def test_clean_fixture_does_not_install_builder_xcb_helpers(self):
        fixture = (ROOT / "scripts/linux-smoke.Dockerfile").read_text(encoding="utf-8")
        install = fixture.split("apt-get install", 1)[1].split("&&", 1)[0]
        for package in ("libportaudio2", "libxcb-cursor0", "libxcb-icccm4",
                        "libxcb-keysyms1", "libxcb-xkb1", "libxkbcommon-x11-0"):
            with self.subTest(package=package):
                self.assertNotIn(package, install.split())


if __name__ == "__main__":
    unittest.main()
