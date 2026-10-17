import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable


class VideoDownloadError(RuntimeError):
    pass


class VideoDownloadCancelled(VideoDownloadError):
    pass


def _python_interpreter() -> str:
    # exe 化時の sys.executable はアプリ本体なので、ソース版CLIの実行には使えない
    if not getattr(sys, "frozen", False):
        return sys.executable
    python = shutil.which("python")
    if not python:
        raise VideoDownloadError(
            "ソース版の動画CLIを実行するにはPythonが必要です。PATHにpythonを追加してください。"
        )
    return python


class VideoDownloader:
    def __init__(
        self,
        project_dir: str,
        output_dir: str | None = None,
        python_path: str | None = None,
        cli_exe_path: str | None = None,
        max_parallel_downloads: int = 3,
    ):
        self.project_dir = Path(project_dir)
        self.cli_dir = self.project_dir / "youtube_dl"
        self.output_dir = Path(output_dir) if output_dir else Path.home() / "Downloads"
        self.python_path = python_path
        self.cli_exe_path = Path(cli_exe_path) if cli_exe_path else None
        self.max_parallel_downloads = max(1, int(max_parallel_downloads))
        self._playlist_support_cache: dict[str, bool] = {}

    @staticmethod
    def _is_runnable_bundle_exe(candidate: Path) -> bool:
        if not candidate.exists():
            return False
        # One-file copied exe cannot run without the bundled _internal runtime folder.
        runtime_dll = candidate.parent / "_internal" / "python311.dll"
        return runtime_dll.exists()

    def _resolve_cli_exe(self) -> Path | None:
        candidates: list[Path] = []
        if self.cli_exe_path:
            candidates.append(self.cli_exe_path)
        candidates.extend([
            self.project_dir / "YoutubeAudioDownloaderCli.exe",
            self.project_dir / "plugin" / "YoutubeAudioDownloader" / "YoutubeAudioDownloaderCli.exe",
            self.project_dir / "dist" / "YoutubeAudioDownloader" / "YoutubeAudioDownloaderCli.exe",
            self.project_dir / "youtube_dl" / "dist" / "YoutubeAudioDownloader" / "YoutubeAudioDownloaderCli.exe",
        ])
        for candidate in candidates:
            if self._is_runnable_bundle_exe(candidate):
                return candidate
        return None

    def _cli_exe_supports_playlist(self, cli_exe: Path) -> bool:
        cache_key = str(cli_exe)
        if cache_key in self._playlist_support_cache:
            return self._playlist_support_cache[cache_key]
        supported = False
        try:
            result = subprocess.run(
                [str(cli_exe), "download", "--help"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            supported = "--playlist" in (result.stdout or "")
        except (OSError, subprocess.TimeoutExpired):
            supported = False
        self._playlist_support_cache[cache_key] = supported
        return supported

    def _resolve_python(self) -> str:
        if self.python_path:
            candidate = Path(self.python_path)
            if candidate.exists():
                return str(candidate)
        venv_candidate = self.cli_dir / "venv" / "Scripts" / "python.exe"
        if venv_candidate.exists():
            return str(venv_candidate)
        project_venv_candidate = self.project_dir / "venv" / "Scripts" / "python.exe"
        if project_venv_candidate.exists():
            return str(project_venv_candidate)
        return _python_interpreter()

    def _ensure_cli_environment(self, need_separation: bool = False) -> str:
        python_exe = self._resolve_python()
        # 音源分離（demucs/torch）は数GB規模のため、ステム指定時のみ要求する。
        probe_modules = "PySide6, yt_dlp" + (", demucs, pydub, PIL" if need_separation else "")
        probe = [python_exe, "-c", f"import {probe_modules}; print('ok')"]
        try:
            subprocess.run(
                probe,
                capture_output=True,
                text=True,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return python_exe
        except (OSError, subprocess.CalledProcessError):
            venv_dir = self.project_dir / "youtube_dl" / "venv"
            if not venv_dir.exists():
                venv_dir.mkdir(parents=True, exist_ok=True)
                bootstrap_python = _python_interpreter()
                subprocess.run(
                    [bootstrap_python, "-m", "venv", str(venv_dir)],
                    check=True,
                    capture_output=True,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            python_candidate = venv_dir / "Scripts" / "python.exe"
            if not python_candidate.exists():
                return python_exe
            packages = ["PySide6>=6.5.0", "yt-dlp>=2024.1.1"]
            if need_separation:
                packages += ["demucs>=4.0.0", "pydub>=0.25.1", "Pillow>=10.0.0"]
            subprocess.run(
                [str(python_candidate), "-m", "pip", "install", *packages, "--disable-pip-version-check"],
                check=True,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return str(python_candidate)

    def _validate_url(self, url: str) -> str:
        normalized = (url or "").strip()
        if not normalized.startswith(("http://", "https://")):
            raise VideoDownloadError("URL形式が正しくありません。http:// または https:// で始まるURLを指定してください。")
        return normalized

    def _run_single_download(
        self,
        url: str,
        audio_only: bool,
        progress_callback: Callable[[str], None] | None = None,
        stems: list[str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        allow_playlist: bool = False,
    ) -> str:
        cli_exe = self._resolve_cli_exe()
        use_bundled_exe = bool(cli_exe) and not (
            allow_playlist and not self._cli_exe_supports_playlist(cli_exe)
        )
        if use_bundled_exe:
            command = [
                str(cli_exe),
                "download",
                url,
                "--output-dir",
                str(self.output_dir),
            ]
            cwd = str(cli_exe.parent)
        else:
            if allow_playlist and cli_exe and not self.cli_dir.exists():
                raise VideoDownloadError(
                    "プレイリスト対応版の動画ダウンロードCLIが未配置です。"
                    "youtube-audio-downloader のCLIを最新版に再ビルドしてください。"
                )
            python_exe = self._ensure_cli_environment(need_separation=bool(stems))
            command = [
                python_exe,
                "cli.py",
                "download",
                url,
                "--output-dir",
                str(self.output_dir),
            ]
            cwd = str(self.cli_dir)

        if not audio_only:
            command.append("--mp4")
        if allow_playlist:
            command.append("--playlist")
        if stems:
            command.extend(["--stems", *stems])
        process = None
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "PYTHONUTF8": "1"},
            )
            # パイプバッファのデッドロックを避けるため別スレッドで読み続ける
            line_queue: queue.Queue[str | None] = queue.Queue()

            def _drain(proc, q):
                try:
                    for line in proc.stdout:
                        q.put(line)
                finally:
                    q.put(None)

            reader = threading.Thread(target=_drain, args=(process, line_queue), daemon=True)
            reader.start()

            output_lines: list[str] = []
            last_report = 0.0
            started_at = time.monotonic()
            while True:
                if cancel_requested and cancel_requested():
                    process.kill()
                    raise VideoDownloadCancelled("ダウンロードをキャンセルしました。")
                try:
                    line = line_queue.get(timeout=1.0)
                    if line is None:
                        break
                    output_lines.append(line)
                except queue.Empty:
                    pass
                elapsed = time.monotonic() - started_at
                if progress_callback and elapsed - last_report >= 5:
                    progress_callback(f"ダウンロード中... 経過 {int(elapsed)}秒")
                    last_report = elapsed
                if elapsed >= 7200:
                    process.kill()
                    raise subprocess.TimeoutExpired(command, 7200)

            process.wait()
            output = "".join(output_lines)
            result_code = process.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VideoDownloadError(f"動画ダウンロードの実行に失敗しました: {exc}") from exc

        if result_code == 0:
            return output.strip() or "動画ダウンロードが完了しました。"

        detail = output.strip() or "不明なエラー"
        if "This video is unavailable" in detail:
            raise VideoDownloadError(
                "YouTube動画を取得できません。URLが正しいか、動画が公開中か、"
                "地域制限・年齢制限・ログイン限定ではないか確認してください。"
            )
        if not cli_exe and "No module named 'PySide6'" in detail:
            raise VideoDownloadError(
                "動画ダウンロードCLIの依存関係が不足しています。"
                "Release の YoutubeAudioDownloaderCli.exe を配置するか、"
                "youtube-audio-downloader 側の依存関係をインストールしてください。\n"
                + detail
            )
        raise VideoDownloadError(f"動画ダウンロードに失敗しました。{detail}")

    def _run_download(
        self,
        urls: list[str],
        audio_only: bool,
        progress_callback: Callable[[str], None] | None = None,
        output_dir: str | None = None,
        stems: list[str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        allow_playlist: bool = False,
    ) -> str:
        targets = [self._validate_url(url) for url in urls]
        if not targets:
            raise VideoDownloadError("ダウンロードするURLが見つかりませんでした。")
        if not self.cli_dir.exists() and not self._resolve_cli_exe():
            raise VideoDownloadError(f"ダウンローダーの実行フォルダが見つかりません: {self.cli_dir}")

        original_output_dir = self.output_dir
        if output_dir:
            self.output_dir = Path(output_dir)
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            worker_count = min(self.max_parallel_downloads, len(targets))
            errors: list[str] = []
            outputs: list[str] = []

            def report(message: str):
                if progress_callback:
                    progress_callback(message)

            report(f"処理開始: {len(targets)}件 / 同時実行 {worker_count}件")

            def run_one(url: str) -> str:
                if cancel_requested and cancel_requested():
                    raise VideoDownloadCancelled("ダウンロードをキャンセルしました。")
                report(f"開始: {url}")
                try:
                    out = self._run_single_download(
                        url, audio_only, report, stems, cancel_requested, allow_playlist
                    )
                except VideoDownloadError:
                    report(f"失敗: {url}")
                    raise
                report(f"完了: {url}")
                return out

            if worker_count == 1:
                for index, url in enumerate(targets, 1):
                    if cancel_requested and cancel_requested():
                        raise VideoDownloadCancelled("ダウンロードをキャンセルしました。")
                    report(f"キュー投入 {index}/{len(targets)}: {url}")
                    try:
                        outputs.append(run_one(url))
                    except VideoDownloadCancelled:
                        raise
                    except VideoDownloadError as exc:
                        errors.append(f"{url}: {exc}")
            else:
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    futures = {}
                    for index, url in enumerate(targets, 1):
                        report(f"キュー投入 {index}/{len(targets)}: {url}")
                        futures[executor.submit(run_one, url)] = url
                    for future, url in futures.items():
                        try:
                            outputs.append(future.result())
                        except VideoDownloadCancelled:
                            raise
                        except VideoDownloadError as exc:
                            errors.append(f"{url}: {exc}")

            if errors:
                raise VideoDownloadError(
                    f"{len(targets)}件中{len(errors)}件のダウンロードに失敗しました。\n" + "\n".join(errors)
                )
            return "\n".join(outputs) if outputs else "動画ダウンロードが完了しました。"
        finally:
            self.output_dir = original_output_dir

    def download(
        self,
        url: str,
        audio_only: bool = True,
        progress_callback: Callable[[str], None] | None = None,
        output_dir: str | None = None,
        stems: list[str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        allow_playlist: bool = False,
    ) -> str:
        return self._run_download(
            [url], audio_only, progress_callback, output_dir, stems,
            cancel_requested, allow_playlist,
        )

    def download_many(
        self,
        urls: list[str],
        audio_only: bool = True,
        progress_callback: Callable[[str], None] | None = None,
        output_dir: str | None = None,
        stems: list[str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        allow_playlist: bool = False,
    ) -> str:
        return self._run_download(
            urls, audio_only, progress_callback, output_dir, stems,
            cancel_requested, allow_playlist,
        )
