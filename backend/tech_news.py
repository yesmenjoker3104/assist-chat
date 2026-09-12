import json
import os
import subprocess
import sys
from pathlib import Path


class TechNewsError(RuntimeError):
    pass


class TechNewsClient:
    def __init__(self, project_dir: str, cli_exe_path: str | None = None):
        self.project_dir = Path(project_dir)
        self.cli_exe_path = Path(cli_exe_path) if cli_exe_path else None

    def _executable(self) -> Path:
        candidates = []
        if self.cli_exe_path:
            candidates.append(self.cli_exe_path)
        candidates.extend([
            self.project_dir / "GetNewTechInfoCli.exe",
            self.project_dir / "dist" / "GetNewTechInfoCli.exe",
        ])
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise TechNewsError("技術記事CLIが見つかりません。Release版CLIを配置してください。")

    def run(self, args: list[str]) -> dict:
        command = [str(self._executable()), *args]
        try:
            result = subprocess.run(
                command,
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "PYTHONUTF8": "1"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TechNewsError(f"技術記事CLIの実行に失敗しました: {exc}") from exc
        output = (result.stdout or "").strip()
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            detail = (result.stderr or output or "出力を解釈できませんでした").strip()
            raise TechNewsError(detail) from exc
        if result.returncode or not payload.get("ok"):
            raise TechNewsError(payload.get("error", "技術記事CLIがエラーを返しました。"))
        return payload

    def search(self, query: str = "", source: str | None = None, unread: bool = False, limit: int = 10) -> dict:
        args = ["search", query, "--limit", str(limit)]
        if source:
            args.extend(["--source", source])
        if unread:
            args.append("--unread")
        return self.run(args)

    def mark_read(self, article_ids: list[int]) -> dict:
        if not article_ids:
            return {"ok": True, "action": "mark_read", "updated": 0}
        return self.run(["mark-read", *(str(article_id) for article_id in article_ids)])

    def fetch(self) -> dict:
        return self.run(["fetch"])

    def list_sources(self) -> dict:
        return self.run(["sources", "list"])

    def count_per_source(self, status: str = "all") -> dict:
        args = ["sources", "count"]
        if status == "unread":
            args.append("--unread")
        elif status == "read":
            args.append("--read")
        return self.run(args)

    def add_source(self, name: str, url: str, category: str | None = None) -> dict:
        args = ["sources", "add", name, url]
        if category:
            args.extend(["--category", category])
        return self.run(args)

    def update_source(self, name: str, url: str | None = None, enabled: bool | None = None, category: str | None = None) -> dict:
        args = ["sources", "update", name]
        if url:
            args.extend(["--url", url])
        if enabled is True:
            args.append("--enable")
        elif enabled is False:
            args.append("--disable")
        if category is not None:
            args.extend(["--category", category])
        return self.run(args)

    def delete_source(self, name: str) -> dict:
        return self.run(["sources", "delete", name])

    def list_categories(self) -> dict:
        return self.run(["categories", "list"])

    def add_category(self, name: str) -> dict:
        return self.run(["categories", "add", name])

    def rename_category(self, old_name: str, new_name: str) -> dict:
        return self.run(["categories", "rename", old_name, new_name])

    def delete_category(self, name: str) -> dict:
        return self.run(["categories", "delete", name])