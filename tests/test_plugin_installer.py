import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import plugin_installer
from backend.plugin_installer import (
    INSTALL_MANIFEST,
    PluginInstallError,
    PluginPackage,
    install_package,
)


def make_package(root: Path, files: dict[str, str], version: str = "1.0.0", preserve=()) -> PluginPackage:
    source = Path(tempfile.mkdtemp(prefix="package-", dir=root))
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return PluginPackage(
        name="Demo",
        version=version,
        description="",
        source_url="",
        source_dir=source,
        work_dir=root,
        preserve=tuple(preserve),
    )


class InstallPackageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="plugin-installer-test-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.plugin_root = self._tmp / "plugins"
        self.target = self.plugin_root / "Demo"

    def read(self, relative: str) -> str:
        return (self.target / relative).read_text(encoding="utf-8")

    def manifest_files(self) -> list[str]:
        data = json.loads((self.target / INSTALL_MANIFEST).read_text(encoding="utf-8"))
        return data["files"]

    def test_fresh_install_copies_files_and_writes_manifest(self):
        package = make_package(self._tmp, {"demo.exe": "v1", "plugin.json": "{}"})
        install_package(package, self.plugin_root, replace=False)
        self.assertEqual(self.read("demo.exe"), "v1")
        self.assertEqual(self.manifest_files(), ["demo.exe", "plugin.json"])

    def test_update_overwrites_and_removes_stale_files(self):
        install_package(make_package(self._tmp, {"a.txt": "old", "b.txt": "old"}, "1.0.0"), self.plugin_root, False)
        install_package(make_package(self._tmp, {"a.txt": "new"}, "2.0.0"), self.plugin_root, True)
        self.assertEqual(self.read("a.txt"), "new")
        self.assertFalse((self.target / "b.txt").exists())
        self.assertEqual(self.manifest_files(), ["a.txt"])

    def test_update_keeps_preserved_files_and_runtime_data(self):
        install_package(
            make_package(self._tmp, {"config.json": "default", "old.txt": "x"}, "1.0.0", preserve=["config.json"]),
            self.plugin_root,
            False,
        )
        (self.target / "config.json").write_text("user edit", encoding="utf-8")
        (self.target / "data").mkdir()
        (self.target / "data" / "app.db").write_text("db", encoding="utf-8")

        install_package(
            make_package(self._tmp, {"config.json": "new default"}, "2.0.0", preserve=["config.json"]),
            self.plugin_root,
            True,
        )
        self.assertEqual(self.read("config.json"), "user edit")
        self.assertFalse((self.target / "old.txt").exists())
        self.assertEqual((self.target / "data" / "app.db").read_text(encoding="utf-8"), "db")

    def test_update_without_manifest_deletes_nothing(self):
        self.target.mkdir(parents=True)
        (self.target / "legacy.txt").write_text("keep", encoding="utf-8")
        install_package(make_package(self._tmp, {"a.txt": "new"}), self.plugin_root, True)
        self.assertEqual(self.read("legacy.txt"), "keep")

    def test_manifest_entries_outside_target_are_ignored(self):
        outside = self.plugin_root / "outside.txt"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("keep", encoding="utf-8")
        self.target.mkdir(parents=True)
        (self.target / INSTALL_MANIFEST).write_text(
            json.dumps({"version": "1.0.0", "files": ["../outside.txt"]}), encoding="utf-8"
        )
        install_package(make_package(self._tmp, {"a.txt": "new"}), self.plugin_root, True)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_locked_file_aborts_before_any_change(self):
        install_package(make_package(self._tmp, {"a.txt": "old", "b.txt": "old"}, "1.0.0"), self.plugin_root, False)
        package = make_package(self._tmp, {"a.txt": "new"}, "2.0.0")
        real_check = plugin_installer._ensure_writable

        def lock_b(path: Path) -> None:
            if path.name == "b.txt":
                raise PluginInstallError("locked")
            real_check(path)

        with mock.patch.object(plugin_installer, "_ensure_writable", side_effect=lock_b):
            with self.assertRaises(PluginInstallError):
                install_package(package, self.plugin_root, True)
        self.assertEqual(self.read("a.txt"), "old")
        self.assertEqual(self.read("b.txt"), "old")
        self.assertEqual(self.manifest_files(), ["a.txt", "b.txt"])

    def test_ensure_writable_reports_os_error_as_install_error(self):
        path = self._tmp / "locked.exe"
        path.write_text("x", encoding="utf-8")
        with mock.patch.object(Path, "open", side_effect=PermissionError("denied")):
            with self.assertRaises(PluginInstallError):
                plugin_installer._ensure_writable(path)

    def test_failure_mid_update_restores_previous_state(self):
        install_package(
            make_package(self._tmp, {"a.txt": "old", "b.txt": "old", "c.txt": "old"}, "1.0.0"),
            self.plugin_root,
            False,
        )
        manifest_before = (self.target / INSTALL_MANIFEST).read_text(encoding="utf-8")
        package = make_package(self._tmp, {"a.txt": "new", "c.txt": "new", "d.txt": "new"}, "2.0.0")
        real_copy2 = shutil.copy2

        def fail_on_c(src, dst, *args, **kwargs):
            if Path(dst).name == "c.txt":
                raise OSError("disk full")
            return real_copy2(src, dst, *args, **kwargs)

        with mock.patch("shutil.copy2", side_effect=fail_on_c):
            with self.assertRaises(OSError):
                install_package(package, self.plugin_root, True)

        self.assertEqual(self.read("a.txt"), "old")
        self.assertEqual(self.read("b.txt"), "old")
        self.assertEqual(self.read("c.txt"), "old")
        self.assertFalse((self.target / "d.txt").exists())
        self.assertEqual((self.target / INSTALL_MANIFEST).read_text(encoding="utf-8"), manifest_before)

    def test_failed_fresh_install_removes_partial_target(self):
        package = make_package(self._tmp, {"a.txt": "new", "b.txt": "new"})
        real_copy2 = shutil.copy2

        def fail_on_b(src, dst, *args, **kwargs):
            if Path(dst).name == "b.txt":
                raise OSError("disk full")
            return real_copy2(src, dst, *args, **kwargs)

        with mock.patch("shutil.copy2", side_effect=fail_on_b):
            with self.assertRaises(OSError):
                install_package(package, self.plugin_root, replace=False)
        self.assertFalse(self.target.exists())

    def test_existing_plugin_without_replace_is_rejected(self):
        install_package(make_package(self._tmp, {"a.txt": "old"}), self.plugin_root, False)
        with self.assertRaises(plugin_installer.PluginExistsError):
            install_package(make_package(self._tmp, {"a.txt": "new"}), self.plugin_root, False)
        self.assertEqual(self.read("a.txt"), "old")


if __name__ == "__main__":
    unittest.main()
