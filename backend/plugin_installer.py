"""Install plugins from a ZIP archive or an HTTPS URL into plugin/."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from backend.plugin_manager import Plugin

CATALOG_URL = "https://raw.githubusercontent.com/yesmenjoker3104/assist-chat/master/plugin_catalog.json"
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
_USER_AGENT = "AssistChat-PluginInstaller"
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
INSTALL_MANIFEST = ".install_manifest.json"


class PluginInstallError(Exception):
    pass


class PluginExistsError(PluginInstallError):
    pass


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    description: str
    url: str
    version: str = ""


@dataclass(frozen=True)
class PluginPackage:
    name: str
    version: str
    description: str
    source_url: str
    source_dir: Path
    work_dir: Path
    preserve: tuple[str, ...] = ()

    def cleanup(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)


def _require_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise PluginInstallError("URLは https:// で始まる必要があります。")


def _open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_catalog(url: str = CATALOG_URL, timeout: float = 10.0) -> list[CatalogEntry]:
    _require_https(url)
    with _open(url, timeout) as response:
        data = json.load(response)
    entries: list[CatalogEntry] = []
    for item in data.get("plugins", []):
        name = str(item.get("name", ""))
        entry_url = str(item.get("url", ""))
        if not _NAME_PATTERN.fullmatch(name) or urlparse(entry_url).scheme != "https":
            continue
        entries.append(
            CatalogEntry(
                name=name,
                description=str(item.get("description", "")),
                url=entry_url,
                version=str(item.get("version", "")),
            )
        )
    return entries


def download_archive(url: str, destination: Path, timeout: float = 60.0) -> Path:
    _require_https(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with _open(url, timeout) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise PluginInstallError("ファイルが大きすぎます（上限300MB）。")
            output.write(chunk)
    with destination.open("rb") as downloaded:
        if downloaded.read(2) != b"PK":
            raise PluginInstallError("ダウンロードしたファイルはZIPではありません。")
    return destination


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as zf:
            members = zf.infolist()
            if sum(member.file_size for member in members) > MAX_EXTRACTED_BYTES:
                raise PluginInstallError("展開後のサイズが大きすぎます。")
            for member in members:
                if not (destination / member.filename).resolve().is_relative_to(root):
                    raise PluginInstallError("不正なパスを含むためインストールを中止しました。")
            zf.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise PluginInstallError("ZIPファイルとして読み込めません。") from exc


def _find_plugin_dir(extract_dir: Path) -> Path:
    if (extract_dir / "plugin.json").is_file():
        return extract_dir
    candidates = [p for p in extract_dir.iterdir() if p.is_dir() and (p / "plugin.json").is_file()]
    if len(candidates) == 1:
        return candidates[0]
    raise PluginInstallError("ZIP内に plugin.json が見つかりません。")


def inspect_archive(archive: Path, source_url: str = "") -> PluginPackage:
    """Extract the archive to a work directory and validate its manifest.

    The caller must call ``PluginPackage.cleanup()`` once the package is no longer needed.
    """
    work_dir = Path(tempfile.mkdtemp(prefix="assist-chat-plugin-"))
    try:
        extract_dir = work_dir / "extract"
        _safe_extract(archive, extract_dir)
        source_dir = _find_plugin_dir(extract_dir)
        plugin = Plugin.load(source_dir)
        if not _NAME_PATTERN.fullmatch(plugin.name):
            raise PluginInstallError(f"プラグイン名が不正です: {plugin.name}")
        if not plugin.executable.resolve().is_relative_to(source_dir.resolve()):
            raise PluginInstallError("executable がプラグインフォルダの外を指しています。")
        if not plugin.executable.is_file():
            raise PluginInstallError(f"実行ファイル {plugin.executable.name} が見つかりません。")
        return PluginPackage(
            name=plugin.name,
            version=plugin.version,
            description=plugin.description,
            source_url=source_url,
            source_dir=source_dir,
            work_dir=work_dir,
            preserve=tuple(plugin.preserve),
        )
    except PluginInstallError:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise PluginInstallError(f"プラグインの読み込みに失敗しました: {exc}") from exc


def fetch_package(url: str, timeout: float = 60.0) -> PluginPackage:
    """Download a plugin ZIP from an HTTPS URL and validate it."""
    _require_https(url)
    download_dir = Path(tempfile.mkdtemp(prefix="assist-chat-download-"))
    try:
        archive = download_archive(url, download_dir / "plugin.zip", timeout)
        return inspect_archive(archive, url)
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


def _package_files(source_dir: Path) -> set[str]:
    return {
        path.relative_to(source_dir).as_posix()
        for path in source_dir.rglob("*")
        if path.is_file() and path.name != INSTALL_MANIFEST
    }


def _is_preserved(relative: str, preserve: tuple[str, ...] | list[str]) -> bool:
    for pattern in preserve:
        prefix = pattern.replace("\\", "/").strip("/")
        if prefix and (relative == prefix or relative.startswith(prefix + "/")):
            return True
    return False


def _read_install_manifest(target: Path) -> set[str]:
    """Return the files a previous install placed in target.

    Entries that resolve outside target are ignored so a tampered manifest cannot delete other files.
    """
    manifest = target / INSTALL_MANIFEST
    if not manifest.is_file():
        return set()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    root = target.resolve()
    files: set[str] = set()
    for relative in data.get("files", []):
        if not isinstance(relative, str):
            continue
        resolved = (target / relative).resolve()
        if resolved != root and resolved.is_relative_to(root):
            files.add(resolved.relative_to(root).as_posix())
    return files


def _remove_installed_file(target: Path, relative: str) -> None:
    path = target / relative
    try:
        path.unlink()
    except FileNotFoundError:
        return
    parent = path.parent
    while parent != target and parent.is_relative_to(target) and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def install_package(package: PluginPackage, plugin_root: Path, replace: bool) -> Path:
    """Copy the package into plugin_root/<name>.

    With replace=True:
    - files shipped by the package are overwritten, except preserved paths that already exist
    - files installed by the previous version (recorded in INSTALL_MANIFEST) that the new
      package no longer ships are deleted, unless preserved
    - files that were never installed by the package (e.g. runtime data) are left untouched
    """
    target = plugin_root / package.name
    if target.exists() and not replace:
        raise PluginExistsError(f"{package.name} は既に存在します。")
    plugin_root.mkdir(parents=True, exist_ok=True)
    new_files = _package_files(package.source_dir)
    if target.exists():
        for relative in sorted(_read_install_manifest(target) - new_files):
            if not _is_preserved(relative, package.preserve):
                _remove_installed_file(target, relative)
    for relative in sorted(new_files):
        destination = target / relative
        if destination.exists() and _is_preserved(relative, package.preserve):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package.source_dir / relative, destination)
    manifest = {"version": package.version, "files": sorted(new_files)}
    (target / INSTALL_MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target
