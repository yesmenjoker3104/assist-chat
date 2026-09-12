import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from typing import Any


class GwsError(RuntimeError):
    pass


class GoogleWorkspace:
    def __init__(self, command: str = "gws"):
        self.command = command

    def _executable(self) -> str:
        found = shutil.which(self.command)
        if found:
            return found
        candidate = os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.19.0-win-x64\gws.cmd"
        )
        if os.path.exists(candidate):
            return candidate
        raise GwsError("gwsコマンドが見つかりません。data/settings.jsonでパスを指定してください。")

    def run(self, args: list[str]) -> str:
        try:
            result = subprocess.run(
                [self._executable(), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GwsError(f"gwsの実行に失敗しました: {exc}") from exc
        if result.returncode:
            raise GwsError((result.stderr or result.stdout or "gwsがエラーを返しました").strip())
        return (result.stdout or "").strip()

    def agenda(
        self,
        target_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        search_query: str | None = None,
    ) -> str:
        def run_query_only(query: str) -> str:
            params = {
                "calendarId": "primary",
                "q": query,
                "singleEvents": True,
                "orderBy": "startTime",
            }
            return self.run(["calendar", "events", "list", "--params", json.dumps(params)])

        if not target_date and not start_date and not search_query:
            return self.run(["calendar", "+agenda"])
        if search_query and not target_date and not start_date:
            return run_query_only(search_query)
        try:
            if start_date:
                start_day = datetime.fromisoformat(start_date).date()
                end_day = datetime.fromisoformat(end_date).date() if end_date else start_day + timedelta(days=1)
            else:
                start_day = datetime.fromisoformat(target_date).date()
                end_day = start_day + timedelta(days=1)
        except (TypeError, ValueError):
            if search_query:
                return run_query_only(search_query)
            raise GwsError("予定の検索期間が正しくありません。YYYY-MM-DD形式で指定してください。")
        if end_day <= start_day:
            if search_query:
                return run_query_only(search_query)
            raise GwsError("予定の検索期間が正しくありません。終了日は開始日より後にしてください。")
        timezone = datetime.now().astimezone().tzinfo
        start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone)
        end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=timezone)
        params = {
            "calendarId": "primary",
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if search_query:
            params["q"] = search_query
        return self.run(["calendar", "events", "list", "--params", json.dumps(params)])

    def create_event(
        self,
        title: str,
        start: str,
        end: str,
        description: str = "",
        all_day: bool = False,
    ) -> str:
        if not title.strip():
            raise GwsError("予定名が空です。")
        event: dict[str, Any] = {"summary": title}
        if all_day:
            try:
                start_date = datetime.fromisoformat(start).date()
                end_date = datetime.fromisoformat(end).date()
            except ValueError as exc:
                raise GwsError("終日予定の日付形式が正しくありません。") from exc
            if end_date <= start_date:
                end_date = start_date + timedelta(days=1)
            event["start"] = {"date": start_date.isoformat()}
            event["end"] = {"date": end_date.isoformat()}
        else:
            try:
                start_time = datetime.fromisoformat(start)
                end_time = datetime.fromisoformat(end)
            except ValueError as exc:
                raise GwsError("予定の日時形式が正しくありません。") from exc
            if end_time <= start_time:
                raise GwsError("予定の終了日時は開始日時より後にしてください。")
            event["start"] = {"dateTime": start, "timeZone": "Asia/Tokyo"}
            event["end"] = {"dateTime": end, "timeZone": "Asia/Tokyo"}
        if description:
            event["description"] = description
        return self.run([
            "calendar", "events", "insert",
            "--params", json.dumps({"calendarId": "primary"}),
            "--json", json.dumps(event, ensure_ascii=False),
        ])

    def delete_event(self, event_id: str) -> str:
        return self.run([
            "calendar", "events", "delete",
            "--params", json.dumps({"calendarId": "primary", "eventId": event_id}),
        ])

    def is_auth_error(self, message: str) -> bool:
        keywords = (
            "Access denied",
            "No credentials",
            "auth login",
            "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE",
            "invalid_grant",
            "Token has been expired or revoked",
            "insufficient authentication scopes",
        )
        return any(k in message for k in keywords)

    def auth_login(self) -> None:
        import os
        import tempfile
        exe = self._executable()
        gcloud_bin = os.path.expandvars(r"%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin")
        bat = tempfile.NamedTemporaryFile(suffix=".bat", delete=False, mode="w", encoding="utf-8")
        bat.write(
            f'@echo off\n'
            f'set PATH={gcloud_bin};%PATH%\n'
            f'"{exe}" auth setup --login\n'
            f'pause\n'
        )
        bat.close()
        os.startfile(bat.name)
