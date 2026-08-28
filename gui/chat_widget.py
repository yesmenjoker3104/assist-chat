import json
import re
from html import unescape
from datetime import datetime, timedelta

from PyQt6.QtCore import QEvent, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from backend.google_calendar import GoogleWorkspace, GwsError
from backend.llm_engine import OllamaEngine
from backend.tech_news import TechNewsClient, TechNewsError
from backend.video_downloader import VideoDownloadError, VideoDownloader

SYSTEM_PROMPT = """あなたはGoogleカレンダー操作アシスタントです。
ユーザーの依頼をJSONだけで返してください。説明文やMarkdownは不要です。
    読み取り: { "action": "agenda", "target_date": "YYYY-MM-DDまたはnull", "start_date": "YYYY-MM-DDまたはnull", "end_date": "YYYY-MM-DDまたはnull", "search_query": "検索語またはnull" }
    作成: { "action": "create", "title": "予定名", "start": "YYYY-MM-DDTHH:MM:SS+09:00", "end": "YYYY-MM-DDTHH:MM:SS+09:00", "description": "", "all_day": false }
    複数作成: { "action": "create", "events": [{ "title": "予定名", "start": "YYYY-MM-DDTHH:MM:SS+09:00", "end": "YYYY-MM-DDTHH:MM:SS+09:00", "description": "", "all_day": false }] }
    削除: { "action": "delete", "event_id": "イベントID" }
    動画ダウンロード: { "action": "download_video", "url": "https://...", "urls": ["https://..."], "audio_only": true, "output_dir": "D:\\Download" }
    技術記事検索: { "action": "tech_news_search", "query": "検索語", "source": null, "unread": false, "limit": 10 }
    技術記事取得: { "action": "tech_news_fetch" }
    通常の質問: { "action": "chat", "reply": "回答" }
    予定検索では、予定名・説明・場所を検索する語をsearch_queryに設定してください。「バンド練習っぽい」のような曖昧な依頼では「バンド 練習 リハーサル スタジオ」など関連語を複数設定します。期間指定があればstart_dateとend_dateも設定してください。
    予定確認では、ユーザーが指定した任意の期間を解釈してください。例えば「今日から3週間」はstart_dateを今日、end_dateを今日から21日後にします。「8月」はその年の8月1日から9月1日未満にします。「今週」「来週」「次の月曜日」なども具体的なYYYY-MM-DDへ変換します。期間指定ではtarget_dateをnullにし、start_dateとend_dateを必ず設定してください。
現在日時: {now}
"""


class LlmThread(QThread):
    completed = pyqtSignal(str)

    def __init__(self, llm: OllamaEngine, prompt: str):
        super().__init__()
        self.llm = llm
        self.prompt = prompt

    def run(self):
        self.completed.emit("".join(self.llm.generate(self.prompt)))


class VideoDownloadThread(QThread):
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(
        self,
        downloader: VideoDownloader,
        urls: list[str],
        audio_only: bool = True,
        output_dir: str | None = None,
        stems: list[str] | None = None,
    ):
        super().__init__()
        self.downloader = downloader
        self.urls = urls
        self.audio_only = audio_only
        self.output_dir = output_dir
        self.stems = stems or []

    def run(self):
        try:
            self.completed.emit(
                self.downloader.download_many(
                    self.urls,
                    self.audio_only,
                    progress_callback=self.progress.emit,
                    output_dir=self.output_dir,
                    stems=self.stems,
                )
            )
        except VideoDownloadError as exc:
            self.failed.emit(str(exc))


class ChatWidget(QWidget):
    def __init__(
        self,
        llm: OllamaEngine,
        gws: GoogleWorkspace,
        downloader: VideoDownloader | None = None,
        tech_news: TechNewsClient | None = None,
    ):
        super().__init__()
        self.llm = llm
        self.gws = gws
        self.downloader = downloader
        self.tech_news = tech_news
        self.thread = None
        self.download_thread = None
        self.download_progress_bubble = None
        self.download_progress_lines = []
        self.last_user_text = ""
        self.thinking_frame = 0
        self.thinking_timer = QTimer(self)
        self.thinking_timer.setInterval(350)
        self.thinking_timer.timeout.connect(self._animate_thinking)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; }")
        self.message_panel = QWidget()
        self.message_layout = QVBoxLayout(self.message_panel)
        self.message_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.message_layout.setContentsMargins(8, 8, 8, 8)
        self.message_layout.setSpacing(6)
        self.scroll_area.setWidget(self.message_panel)
        layout.addWidget(self.scroll_area, 1)
        self.thinking_bubble = None
        self.input = QTextEdit()
        self.input.setPlaceholderText("例: 今日の予定を見せて / 明日15時から会議を追加して")
        self.input.setFixedHeight(90)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)
        self.send_button = QPushButton("送信")
        self.send_button.clicked.connect(self.send)
        layout.addWidget(self.send_button)

    def eventFilter(self, watched, event):
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.send()
                    return True
        return super().eventFilter(watched, event)

    def send(self):
        text = self.input.toPlainText().strip()
        rich_text = self.input.toHtml()
        if not text or self.thread and self.thread.isRunning():
            return
        self.input.clear()
        self.last_user_text = text
        self._add_bubble("user", text)
        direct_urls = self._extract_urls(text, rich_text)
        direct_video_urls = [
            url for url in direct_urls
            if re.match(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", url, re.IGNORECASE)
        ]
        if direct_video_urls:
            self._download_video({
                "urls": direct_video_urls,
                "audio_only": self._is_audio_only_request(text),
                "output_dir": self._extract_output_dir(text),
                "stems": self._extract_stems(text),
            })
            return
        prompt = SYSTEM_PROMPT.replace("{now}", datetime.now().astimezone().isoformat()) + f"\n依頼: {text}"
        self.send_button.setEnabled(False)
        self.thinking_bubble = self._add_bubble("assistant", "・")
        self.thinking_frame = 0
        self.thinking_timer.start()
        self.thread = LlmThread(self.llm, prompt)
        self.thread.completed.connect(self.handle_response)
        self.thread.start()

    def handle_response(self, response: str):
        self.thinking_timer.stop()
        self._remove_bubble(self.thinking_bubble)
        self.thinking_bubble = None
        command = self._parse_json(response)
        command = self._apply_relative_agenda(command)
        try:
            if not command:
                self._add_bubble("assistant", response)
            elif command.get("action") == "agenda":
                raw_events = self.gws.agenda(
                    command.get("target_date"),
                    command.get("start_date"),
                    command.get("end_date"),
                    command.get("search_query"),
                )
                self._add_bubble(
                    "assistant",
                    self._format_agenda(raw_events),
                )
            elif command.get("action") == "download_video":
                self._download_video(command)
            elif command.get("action") == "tech_news_search":
                self._search_tech_news(command)
            elif command.get("action") == "tech_news_fetch":
                self._fetch_tech_news()
            elif command.get("action") == "create":
                self._confirm_create(command)
            elif command.get("action") == "delete":
                self._confirm_delete(command)
            elif command.get("action") == "chat":
                self._add_bubble("assistant", command.get("reply", ""))
            else:
                self._add_bubble("assistant", "操作を解釈できませんでした。")
        except GwsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")
        finally:
            self.send_button.setEnabled(True)

    def _search_tech_news(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        try:
            result = self.tech_news.search(
                str(command.get("query", "")),
                command.get("source"),
                bool(command.get("unread", False)),
                int(command.get("limit", 10)),
            )
            articles = result.get("articles", [])
            if not articles:
                self._add_bubble("assistant", "該当する技術記事はありません。")
                return
            lines = [f"技術記事（{len(articles)}件）"]
            for article in articles:
                lines.append(f"・{article.get('title', '無題')}\n  {article.get('source', '')} | {article.get('url', '')}")
            self._add_bubble("assistant", "\n".join(lines))
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _fetch_tech_news(self):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        try:
            result = self.tech_news.fetch()
            self._add_bubble("assistant", f"技術記事を取得しました。新規追加: {result.get('saved', 0)}件")
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _download_video(self, command: dict):
        if not self.downloader:
            self._add_bubble("assistant", "動画ダウンロード機能の設定が未完了です。")
            return
        if self.download_thread and self.download_thread.isRunning():
            self._add_bubble("assistant", "現在ダウンロード処理中です。完了までお待ちください。")
            return

        urls = []
        raw_urls = command.get("urls")
        if isinstance(raw_urls, list):
            urls.extend(str(url).strip() for url in raw_urls if str(url).strip())
        single_url = str(command.get("url", "")).strip()
        if single_url:
            urls.append(single_url)
        if not urls:
            self._add_bubble("assistant", "ダウンロードするURLが見つかりませんでした。")
            return
        urls = list(dict.fromkeys(urls))

        audio_only = bool(command.get("audio_only", True))
        raw_output_dir = command.get("output_dir")
        output_dir = str(raw_output_dir).strip() if raw_output_dir else None
        stems = self._normalize_stems(command.get("stems"))
        mode_label = "音源のみ" if audio_only else "動画+音源"
        if stems:
            mode_label += f" / 音源分離: {', '.join(stems)}"
        lines = "\n".join(urls)
        destination = output_dir if output_dir else "(既定) Downloads"
        self.download_progress_lines = [
            f"{mode_label}のダウンロードを開始します（{len(urls)}件）。",
            f"保存先: {destination}",
            *urls,
            "状態: 準備中...",
        ]
        self.download_progress_bubble = self._add_bubble("assistant", "\n".join(self.download_progress_lines))
        self.send_button.setEnabled(False)
        self.download_thread = VideoDownloadThread(self.downloader, urls, audio_only, output_dir, stems)
        self.download_thread.progress.connect(self._on_download_progress)
        self.download_thread.completed.connect(self._on_download_completed)
        self.download_thread.failed.connect(self._on_download_failed)
        self.download_thread.start()

    def _on_download_progress(self, message: str):
        if not self.download_progress_bubble:
            return
        if not message:
            return
        self.download_progress_lines[-1] = message
        self.download_progress_bubble.setText("\n".join(self.download_progress_lines))
        row = self.download_progress_bubble.parentWidget()
        if row is not None:
            QTimer.singleShot(0, lambda: self._scroll_to_widget(row))

    def _on_download_completed(self, output: str):
        if self.download_progress_bubble:
            self.download_progress_lines[-1] = "完了: すべてのダウンロードが終了しました。"
            self.download_progress_bubble.setText("\n".join(self.download_progress_lines))
        lines = [line for line in output.splitlines() if line.strip()]
        summary = "\n".join(lines[-8:]) if lines else "動画ダウンロードが完了しました。"
        self._add_bubble("assistant", f"ダウンロード結果\n{summary}")
        self.download_thread = None
        self.send_button.setEnabled(True)

    def _on_download_failed(self, message: str):
        if self.download_progress_bubble:
            self.download_progress_lines[-1] = "失敗: 一部または全部のダウンロードに失敗しました。"
            self.download_progress_bubble.setText("\n".join(self.download_progress_lines))
        self._add_bubble("assistant", f"エラー: {message}")
        self.download_thread = None
        self.send_button.setEnabled(True)

    @staticmethod
    def _extract_urls(text: str, rich_text: str = "") -> list[str]:
        urls: list[str] = []

        # Support markdown-style links: [title](https://example.com)
        markdown_matches = re.findall(r"\[[^\]]+\]\((https?://[^\s)]+)\)", text)
        urls.extend(markdown_matches)

        # Also support raw URLs pasted directly in text.
        raw_matches = re.findall(r"https?://\S+", text)
        urls.extend(item.rstrip(")]}.,!?") for item in raw_matches)

        # Rich-text paste can keep the href out of toPlainText().
        href_matches = re.findall(r'href=["\'](https?://[^"\']+)', rich_text, re.IGNORECASE)
        urls.extend(unescape(item) for item in href_matches)

        return list(dict.fromkeys(urls))

    @staticmethod
    def _extract_output_dir(text: str) -> str | None:
        match = re.search(
            r"([A-Za-z]:[\\/][^\r\n]+?)(?=に(?:ダウンロード|保存|取得)|\s+https?://|$)",
            text,
        )
        if not match:
            return None
        candidate = match.group(1).strip().rstrip("に。.,!?")
        return candidate or None

    @staticmethod
    def _is_audio_only_request(text: str) -> bool:
        lowered = text.lower()
        explicit_video = re.search(r"動画(?:も|と音源)?(?:を)?(?:ほしい|ください|お願い|ダウンロード)", text)
        if explicit_video or "mp4" in lowered:
            return False
        audio_only_keywords = ("音源", "音声", "音だけ", "mp3")
        return any(keyword in text for keyword in audio_only_keywords)

    @staticmethod
    def _normalize_stems(value) -> list[str]:
        if not isinstance(value, list):
            return []
        allowed = {"vocals", "drums", "bass", "guitar", "piano", "other"}
        return list(dict.fromkeys(str(item).lower() for item in value if str(item).lower() in allowed))

    @classmethod
    def _extract_stems(cls, text: str) -> list[str]:
        stem_names = {
            "ボーカル": "vocals", "ヴォーカル": "vocals", "歌": "vocals",
            "ドラム": "drums", "ドラムス": "drums",
            "ベース": "bass", "ギター": "guitar", "ピアノ": "piano",
            "その他": "other",
        }
        stems = [stem for name, stem in stem_names.items() if name in text]
        english_matches = re.findall(r"(?i)\b(vocals|drums|bass|guitar|piano|other)\b", text)
        return cls._normalize_stems(stems + english_matches)

    @staticmethod
    def _is_download_request(text: str) -> bool:
        trigger_keywords = ("ダウンロード", "取得", "保存", "頂戴", "ちょうだい")
        if any(keyword in text for keyword in trigger_keywords):
            return True
        return bool(re.search(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", text, re.IGNORECASE))

    def _apply_relative_agenda(self, command: dict | None) -> dict | None:
        if not any(word in self.last_user_text for word in ("予定", "スケジュール", "カレンダー")):
            return command
        today = datetime.now().astimezone().date()
        base = command if isinstance(command, dict) else {}
        if "来週" in self.last_user_text and any(word in self.last_user_text for word in ("一週間", "1週間", "一週", "週間")):
            next_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
            return {
                **base,
                "action": "agenda",
                "target_date": None,
                "start_date": next_monday.isoformat(),
                "end_date": (next_monday + timedelta(days=7)).isoformat(),
                "search_query": None,
            }
        weekdays = {"月曜": 0, "月曜日": 0, "火曜": 1, "火曜日": 1, "水曜": 2, "水曜日": 2,
                    "木曜": 3, "木曜日": 3, "金曜": 4, "金曜日": 4, "土曜": 5, "土曜日": 5,
                    "日曜": 6, "日曜日": 6}
        weekday_name = next((name for name in weekdays if name in self.last_user_text), None)
        if weekday_name is None:
            return command
        days_ahead = (weekdays[weekday_name] - today.weekday()) % 7
        if days_ahead == 0 and any(word in self.last_user_text for word in ("今度の", "次の")):
            days_ahead = 7
        target = today + timedelta(days=days_ahead)
        return {**base, "action": "agenda", "target_date": target.isoformat(), "start_date": None, "end_date": None}

    def _confirm_create(self, command: dict):
        events = command.get("events")
        if not isinstance(events, list) or not events:
            events = [command]
        events = [event for event in events if isinstance(event, dict)]
        if not events:
            self._add_bubble("assistant", "登録する予定を解釈できませんでした。")
            return
        lines = []
        for index, event in enumerate(events, 1):
            title = event.get("title", "無題")
            suffix = "（終日）" if event.get("all_day") else ""
            lines.append(f"{index}. {title}\n   {event.get('start', '')} ～ {event.get('end', '')}{suffix}")
        answer = QMessageBox.question(
            self, "予定を追加",
            f"次の{len(events)}件の予定を追加しますか？\n\n" + "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            created = 0
            for event in events:
                self.gws.create_event(
                    event.get("title", "無題"),
                    event.get("start", ""),
                    event.get("end", ""),
                    event.get("description", ""),
                    bool(event.get("all_day", False)),
                )
                created += 1
            self._add_bubble("assistant", f"{created}件の予定を追加しました。")
        else:
            self._add_bubble("assistant", "追加をキャンセルしました。")

    def _confirm_delete(self, command: dict):
        event_id = command.get("event_id", "")
        answer = QMessageBox.question(
            self, "予定を削除", f"イベントID {event_id} の予定を削除しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.gws.delete_event(event_id)
            self._add_bubble("assistant", "予定を削除しました。")
        else:
            self._add_bubble("assistant", "削除をキャンセルしました。")

    def _add_bubble(self, role: str, text: str) -> QLabel:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setMaximumWidth(560)
        if role == "user":
            bubble.setStyleSheet("background:#89b4fa; color:#1e1e2e; border-radius:12px; padding:10px 14px;")
            row_layout.addStretch()
            row_layout.addWidget(bubble)
        else:
            bubble.setStyleSheet("background:#313244; color:#cdd6f4; border-radius:12px; padding:10px 14px;")
            row_layout.addWidget(bubble)
            row_layout.addStretch()
        self.message_layout.addWidget(row)
        QTimer.singleShot(0, lambda: self._scroll_to_widget(row))
        return bubble

    def _remove_bubble(self, bubble: QLabel | None):
        if bubble is None:
            return
        row = bubble.parentWidget()
        if row is not None:
            self.message_layout.removeWidget(row)
            row.deleteLater()

    def _scroll_to_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _scroll_to_widget(self, widget: QWidget):
        if widget is not None:
            self.scroll_area.ensureWidgetVisible(widget, 0, 0)

    def _animate_thinking(self):
        self.thinking_frame = (self.thinking_frame + 1) % 4
        self.thinking_bubble.setText("・" * (self.thinking_frame + 1))

    @staticmethod
    def _format_agenda(raw_events: str) -> str:
        try:
            payload = json.loads(raw_events)
        except json.JSONDecodeError:
            return raw_events

        events = payload.get("items", []) if isinstance(payload, dict) else []
        if not events:
            return "予定はありません。"

        lines = [f"予定一覧（{len(events)}件）"]
        for event in events:
            start_info = event.get("start", {})
            end_info = event.get("end", {})
            if "date" in start_info:
                when = f"{start_info['date']}（終日）"
            else:
                start = ChatWidget._format_datetime(start_info.get("dateTime"))
                end = ChatWidget._format_datetime(end_info.get("dateTime"))
                when = f"{start} ～ {end}" if end else start
            title = event.get("summary") or "（タイトルなし）"
            lines.append(f"・{when}  {title}")
            if event.get("location"):
                lines.append(f"  場所: {event['location']}")
            if event.get("description"):
                description = " ".join(event["description"].split())
                lines.append(f"  詳細: {description}")
        return "\n".join(lines)

    @staticmethod
    def _format_datetime(value: str | None) -> str:
        if not value:
            return "日時不明"
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone().strftime("%m/%d %H:%M")
        except ValueError:
            return value

    @staticmethod
    def _parse_json(response: str) -> dict | None:
        cleaned = response.strip().replace("```json", "").replace("```", "").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
