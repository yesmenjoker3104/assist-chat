import html
import json
import re
import threading
from datetime import datetime

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QWIDGETSIZE_MAX,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from backend.chat_history import ChatHistory
from backend.llm_engine import OllamaEngine
from backend.plugin_manager import PluginAction, PluginManager
from backend.tech_news import TechNewsClient, TechNewsError
from config import HISTORY_DATABASE_FILE, load_history_limits, load_settings

_URL_RE = re.compile(r'(https?://[^\s<>"]+)')


def _to_rich(text: str) -> str:
    """プレーンテキストをRichText用HTMLに変換。URLはクリッカブルリンクにする。"""
    escaped = html.escape(text)
    escaped = _URL_RE.sub(r'<a href="\1" style="color:#89dceb;">\1</a>', escaped)
    return escaped.replace("\n", "<br>")


def _extract_urls(text: str, rich_text: str = "") -> list[str]:
    urls: list[str] = []
    urls.extend(re.findall(r"\[[^\]]+\]\((https?://[^\s)]+)\)", text))
    urls.extend(item.rstrip(")]}.,!?") for item in re.findall(r"https?://\S+", text))
    urls.extend(
        html.unescape(item)
        for item in re.findall(r'href=["\'](https?://[^"\']+)', rich_text, re.IGNORECASE)
    )
    return list(dict.fromkeys(urls))


# カレンダー等の固定アクションは各プラグインの plugin.json から動的挿入される
SYSTEM_PROMPT_TEMPLATE = """あなたはアシスタントです。
ユーザーの依頼をJSONだけで返してください。説明文やMarkdownは不要です。
{plugin_actions}
    モデル切り替え: {{ "action": "model_switch", "model": "モデル名（例:llama3.2:3b）" }}
    会話履歴削除: {{ "action": "chat_history_delete" }}
    通常の質問: {{ "action": "chat", "reply": "回答" }}
現在日時: {now}
"""


class LlmThread(QThread):
    chunk_received = pyqtSignal(str)
    completed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, llm: OllamaEngine, prompt: str):
        super().__init__()
        self.llm = llm
        self.prompt = prompt

    def run(self):
        chunks = []
        for chunk in self.llm.generate(self.prompt):
            if self.isInterruptionRequested():
                self.cancelled.emit()
                return
            chunks.append(chunk)
            self.chunk_received.emit(chunk)
        if self.isInterruptionRequested():
            self.cancelled.emit()
            return
        self.completed.emit("".join(chunks))



class TechNewsFetchThread(QThread):
    completed = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, tech_news: TechNewsClient):
        super().__init__()
        self.tech_news = tech_news

    def run(self):
        try:
            self.completed.emit(self.tech_news.fetch())
        except TechNewsError as exc:
            self.failed.emit(str(exc))


class TechNewsSearchThread(QThread):
    completed = pyqtSignal(list)
    no_results = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, tech_news: TechNewsClient, command: dict):
        super().__init__()
        self.tech_news = tech_news
        self.command = command

    def _match_source(self, name: str) -> str | None:
        lowered = name.lower()
        for src in self.tech_news.list_sources().get("sources", []):
            src_name = src.get("name", "")
            if src_name.lower() == lowered:
                return src_name
        return None

    def _match_category(self, query: str) -> dict | None:
        if not query.strip():
            return None
        normalized = re.sub(r"[\s・、】【]", "", query).lower()
        for cat in self.tech_news.list_categories().get("categories", []):
            name = str(cat.get("name", ""))
            norm_name = re.sub(r"[\s・、】【]", "", name).lower()
            if norm_name and norm_name in normalized:
                return cat
        return None

    def run(self):
        try:
            limit = max(1, min(100, int(self.command.get("limit") or 10)))
            source = self.command.get("source") or None
            sources = self.command.get("sources")
            query_str = str(self.command.get("query", ""))
            unread = bool(self.command.get("unread", False))

            category = self._match_category(query_str)
            if not category and source:
                category = self._match_category(str(source))
            if not category and isinstance(sources, list):
                for s in sources:
                    category = self._match_category(str(s))
                    if category:
                        break

            if category:
                articles = []
                for cat_source in category.get("sources", []):
                    if cat_source.get("enabled"):
                        result = self.tech_news.search("", cat_source["name"], unread, limit)
                        articles.extend(result.get("articles", []))
                if not articles:
                    self.no_results.emit(f"カテゴリ「{category['name']}」に該当する技術記事はありません。")
                    return
                self.completed.emit(articles)
                return

            if isinstance(sources, list) and sources:
                articles = []
                for req_src in dict.fromkeys(str(s).strip() for s in sources if str(s).strip()):
                    matched = self._match_source(req_src)
                    if matched:
                        result = self.tech_news.search("", matched, unread, limit)
                        articles.extend(result.get("articles", []))
                if not articles:
                    self.no_results.emit("指定されたソースに該当する技術記事はありません。")
                    return
                self.completed.emit(articles)
                return

            result = self.tech_news.search(query_str, source, unread, limit)
            articles = result.get("articles", [])
            if not articles and not source and query_str.strip():
                matched = self._match_source(query_str.strip())
                if matched:
                    result = self.tech_news.search("", matched, unread, limit)
                    articles = result.get("articles", [])
            if not articles:
                hint = f"（source={source!r}, query={query_str!r}, limit={limit}）"
                self.no_results.emit(f"該当する技術記事はありません。{hint}")
                return
            self.completed.emit(articles)
        except TechNewsError as exc:
            self.failed.emit(str(exc))


class RoutingThread(QThread):
    """送信テキストをバックグラウンドで解析し、適切なルートへシグナルを発火する。
    メインスレッドへのブロッキングI/Oを防ぐため、軽い入力判定だけをここで行う。"""

    route_llm = pyqtSignal(str)
    route_history_clear = pyqtSignal()

    def __init__(self, text: str, rich_text: str, prompt: str):
        super().__init__()
        self.text = text
        self.rich_text = rich_text
        self.prompt = prompt

    def run(self):
        text = self.text
        rich_text = self.rich_text

        if re.search(r"(?:会話|チャット).*(?:履歴|ログ).*(?:削除|消去|クリア)", text):
            self.route_history_clear.emit()
            return

        direct_urls = _extract_urls(text, rich_text)
        link_urls = [url for url in direct_urls if url not in text]
        prompt = self.prompt
        if link_urls:
            prompt += (
                "\n\n本文中のリンク先（ユーザーがリンクとして指定したURL。この値をそのまま使うこと）:\n"
                + "\n".join(link_urls)
            )
        self.route_llm.emit(prompt)


class ConfirmRequest:
    """プラグインCLIからの確認要求。メインスレッドで回答を入れ、ワーカースレッドへ通知する。"""

    def __init__(self, title: str, message: str):
        self.title = title
        self.message = message
        self.confirmed = False
        self.event = threading.Event()


class PluginActionThread(QThread):
    """プラグインCLIをバックグラウンドで実行する。CLI からの LLM 依頼はこのスレッド内で処理する。"""

    completed = pyqtSignal(dict)
    cancelled = pyqtSignal()
    progress = pyqtSignal(str, str)  # (progress_id, text)
    confirm_requested = pyqtSignal(object)  # ConfirmRequest

    def __init__(self, plugin_manager: PluginManager, plugin, action_def: PluginAction, command: dict, llm: OllamaEngine):
        super().__init__()
        self.plugin_manager = plugin_manager
        self.plugin = plugin
        self.action_def = action_def
        self.command = command
        self.llm = llm

    def run(self):
        def _progress_cb(pid: str, text: str) -> None:
            self.progress.emit(pid, text)

        def _confirm_cb(title: str, message: str) -> bool:
            request = ConfirmRequest(title, message)
            self.confirm_requested.emit(request)
            # 回答待ち中もキャンセル要求を拾えるよう短い間隔で待つ
            while not request.event.wait(0.2):
                if self.isInterruptionRequested():
                    return False
            return request.confirmed

        result = self.plugin_manager.execute(
            self.plugin,
            self.action_def,
            self.command,
            llm_complete=self.llm.complete,
            is_cancelled=self.isInterruptionRequested,
            progress_callback=_progress_cb,
            confirm_callback=_confirm_cb,
        )
        if self.isInterruptionRequested():
            self.cancelled.emit()
            return
        self.completed.emit(result)


class HistoryLoadThread(QThread):
    completed = pyqtSignal(list)

    def __init__(self, history: ChatHistory, before_id: int, limit: int):
        super().__init__()
        self.history = history
        self.before_id = before_id
        self.limit = limit

    def run(self):
        self.completed.emit(self.history.older(self.before_id, self.limit))


class ChatWidget(QWidget):
    def __init__(
        self,
        llm: OllamaEngine,
        tech_news: TechNewsClient | None = None,
        plugin_manager: PluginManager | None = None,
        tech_news_auto_fetch_enabled: bool = True,
        tech_news_auto_fetch_minutes: int = 30,
    ):
        super().__init__()
        self.llm = llm
        self.tech_news = tech_news
        self.plugin_manager = plugin_manager
        settings = load_settings()
        limits = load_history_limits()
        self.history = ChatHistory(HISTORY_DATABASE_FILE)
        self.history_retention = min(
            int(settings.get("chat_history_retention", 5000)), int(limits["max_retention"])
        )
        self._history_writes_since_prune = 0
        self.history_llm_turns = min(
            int(settings.get("chat_history_llm_turns", 10)), int(limits["max_llm_turns"])
        )
        self.history_initial_messages = min(
            int(settings.get("chat_history_initial_messages", 200)), int(limits["max_initial_messages"])
        )
        self._history_oldest_id = None
        self._history_loading = False
        self._history_exhausted = False
        self._prompt_history = []
        self._prompt_history_index = -1
        self._prompt_history_draft = ""
        self._prompt_suggestions_visible = False
        self._bubble_texts = {}
        self._search_match_bubbles = []
        self._search_matches = []
        self._search_match_index = -1
        self.thread = None
        self.display_thread = None
        self._display_pending = False
        self.plugin_thread = None
        self.plugin_progress_bubble = None
        self.plugin_progress_lines: list[str] = []
        self.last_user_text = ""
        self._pending_focus_widget = None
        self._focus_scheduled = False
        self._focus_generation = 0
        self._streaming_response = ""
        self._is_streaming = False
        self.thinking_frame = 0
        self.thinking_timer = QTimer(self)
        self.thinking_timer.setInterval(350)
        self.thinking_timer.timeout.connect(self._animate_thinking)
        self._all_bubbles: list[QLabel] = []
        self._bubble_reflow_timer = QTimer(self)
        self._bubble_reflow_timer.setSingleShot(True)
        self._bubble_reflow_timer.setInterval(120)
        self._bubble_reflow_timer.timeout.connect(self._reflow_bubbles)
        self.routing_thread = None
        self.tech_news_fetch_thread = None
        self.tech_news_search_thread = None
        self.tech_news_timer = QTimer(self)
        interval_minutes = max(1, int(tech_news_auto_fetch_minutes))
        self.tech_news_timer.setInterval(interval_minutes * 60 * 1000)
        self.tech_news_timer.timeout.connect(self._fetch_tech_news_automatically)
        if self.tech_news and tech_news_auto_fetch_enabled:
            self.tech_news_timer.start()
        self._build_ui()
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.search_shortcut.activated.connect(self._toggle_history_search)
        self._load_prompt_history()
        self._restore_initial_history()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        history_controls = QHBoxLayout()
        self.history_search_bar = QWidget()
        self.history_search_bar.setLayout(history_controls)
        self.history_search_input = QLineEdit()
        self.history_search_input.installEventFilter(self)
        self.history_search_input.setPlaceholderText("会話履歴を検索")
        self.history_search_input.textChanged.connect(self._search_history)
        self.history_search_input.returnPressed.connect(self._search_history)
        history_controls.addWidget(self.history_search_input, 1)
        search_button = QPushButton("検索")
        search_button.clicked.connect(self._search_history)
        history_controls.addWidget(search_button)
        previous_match_button = QPushButton("↑")
        previous_match_button.setToolTip("前の一致箇所")
        previous_match_button.clicked.connect(lambda: self._focus_search_match(-1))
        history_controls.addWidget(previous_match_button)
        next_match_button = QPushButton("↓")
        next_match_button.setToolTip("次の一致箇所")
        next_match_button.clicked.connect(lambda: self._focus_search_match(1))
        history_controls.addWidget(next_match_button)
        clear_history_button = QPushButton("会話履歴を削除")
        clear_history_button.clicked.connect(self._clear_history)
        history_controls.addWidget(clear_history_button)
        self.history_search_bar.hide()
        layout.addWidget(self.history_search_bar)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; }")
        self.message_panel = QWidget()
        self.message_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.message_layout = QVBoxLayout(self.message_panel)
        self.message_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.message_layout.setContentsMargins(8, 8, 8, 8)
        self.message_layout.setSpacing(6)
        self.scroll_area.setWidget(self.message_panel)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_history_scroll)
        self.scroll_area.verticalScrollBar().valueChanged.connect(lambda _value: self._update_bubble_visibility())
        layout.addWidget(self.scroll_area, 1)
        self.thinking_bubble = None
        self.input = QTextEdit()
        self.input.setPlaceholderText("例: 今日の予定を見せて / 明日15時から会議を追加して")
        self.input.setFixedHeight(90)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)
        self.prompt_suggestions = QListWidget()
        self.prompt_suggestions.setParent(self)
        self.prompt_suggestions.setWindowFlags(Qt.WindowType.ToolTip)
        self.prompt_suggestions.setMaximumHeight(120)
        self.prompt_suggestions.hide()
        self.prompt_suggestions.itemClicked.connect(self._select_prompt_suggestion)
        self.input.textChanged.connect(self._update_prompt_suggestions)
        self.send_button = QPushButton("送信")
        self.send_button.clicked.connect(self.send)
        self.cancel_button = QPushButton("キャンセル")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_current_operation)
        buttons = QHBoxLayout()
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def _restore_initial_history(self):
        for message in self.history.latest(self.history_initial_messages):
            self._add_bubble(
                message["role"], message["content"],
                is_html=bool(message.get("is_html")), persist=False, focus=False,
            )
            self._history_oldest_id = message["id"]
        if self.history_initial_messages == 0:
            self._history_exhausted = True
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _on_history_scroll(self, value: int):
        if value == 0 and self._history_oldest_id and not self._history_loading and not self._history_exhausted:
            self._load_older_history()

    def _load_older_history(self):
        self._history_loading = True
        scroll_bar = self.scroll_area.verticalScrollBar()
        self._history_previous_scroll_max = scroll_bar.maximum()
        self._history_previous_scroll_value = scroll_bar.value()
        self.history_load_thread = HistoryLoadThread(
            self.history, self._history_oldest_id, self.history_initial_messages
        )
        self.history_load_thread.completed.connect(self._on_older_history_loaded)
        self.history_load_thread.start()

    def _on_older_history_loaded(self, messages: list):
        for message in reversed(messages):
            self._add_bubble(
                message["role"], message["content"],
                is_html=bool(message.get("is_html")),
                persist=False, at_top=True, focus=False,
            )
        if messages:
            self._history_oldest_id = messages[0]["id"]
        else:
            self._history_exhausted = True
        self._history_loading = False
        self.history_load_thread = None
        QTimer.singleShot(0, self._restore_history_scroll_position)

    def _restore_history_scroll_position(self):
        scroll_bar = self.scroll_area.verticalScrollBar()
        scroll_bar.setValue(
            self._history_previous_scroll_value
            + scroll_bar.maximum()
            - self._history_previous_scroll_max
        )

    def apply_history_settings(self, settings: dict):
        limits = load_history_limits()
        self.history_retention = min(int(settings.get("chat_history_retention", 5000)), int(limits["max_retention"]))
        self.history_llm_turns = min(int(settings.get("chat_history_llm_turns", 10)), int(limits["max_llm_turns"]))
        self.history_initial_messages = min(int(settings.get("chat_history_initial_messages", 200)), int(limits["max_initial_messages"]))
        self.history.prune(self.history_retention)

    def _bubble_max_width(self) -> int:
        # ウィンドウ中央を境にAI/自分の側から絶対にはみ出さないよう、幅の半分を上限にする。
        usable = max(self.width() - 48, 300)
        return max(220, usable // 2 - 16)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # ドラッグ中は毎ピクセルの再計算を避け、サイズが落ち着いてから一度だけ反映する。
        self._bubble_reflow_timer.start()
        self._position_prompt_suggestions()
        self._update_bubble_visibility()

    def _reflow_bubbles(self):
        max_width = self._bubble_max_width()
        for bubble in list(self._all_bubbles):
            if sip.isdeleted(bubble):
                continue
            bubble.setMaximumWidth(max_width)
            if not self._bubble_texts.get(bubble, ("assistant", "", False))[2]:
                content = self._bubble_texts[bubble][1]
                if "\n" not in content:
                    bubble.setMinimumWidth(min(max_width, self._bubble_content_width(bubble, content)))
        self._refresh_hidden_row_heights()
        self._update_bubble_visibility()

    def _update_bubble_visibility(self):
        # 表示範囲の上下1画面分の外にあるバブルは非表示にし、行の高さだけ残して
        # スクロール範囲を変えずに、画面外のレイアウト計算を省く。
        if not self._all_bubbles:
            return
        bar = self.scroll_area.verticalScrollBar()
        viewport_height = self.scroll_area.viewport().height()
        self.message_layout.activate()
        top = bar.value() - viewport_height
        bottom = bar.value() + viewport_height * 2
        for bubble in self._all_bubbles:
            if sip.isdeleted(bubble):
                continue
            row = bubble.parentWidget()
            if row is None or sip.isdeleted(row):
                continue
            on_screen = row.y() + row.height() >= top and row.y() <= bottom
            if on_screen:
                if bubble.isHidden():
                    row.setMinimumHeight(0)
                    row.setMaximumHeight(QWIDGETSIZE_MAX)
                    bubble.show()
            elif not bubble.isHidden():
                row.setFixedHeight(row.height())
                bubble.hide()

    def _refresh_hidden_row_heights(self):
        max_width = self._bubble_max_width()
        for bubble in self._all_bubbles:
            if sip.isdeleted(bubble) or not bubble.isHidden():
                continue
            row = bubble.parentWidget()
            if row is None or sip.isdeleted(row):
                continue
            height = bubble.heightForWidth(max_width)
            if height > 0:
                row.setFixedHeight(height)

    @staticmethod
    def _bubble_content_width(bubble: QLabel, text: str) -> int:
        return bubble.fontMetrics().horizontalAdvance(text) + 40

    def eventFilter(self, watched, event):
        input_widget = getattr(self, "input", None)
        search_input = getattr(self, "history_search_input", None)
        if watched is input_widget and event.type() == QEvent.Type.KeyPress:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_F:
                self._toggle_history_search()
                return True
            if self._prompt_suggestions_visible and event.key() == Qt.Key.Key_Escape:
                self.prompt_suggestions.hide()
                self._prompt_suggestions_visible = False
                return True
            if event.key() == Qt.Key.Key_Up and self._cursor_is_at_start():
                if self._navigate_prompt_history(1):
                    return True
            if event.key() == Qt.Key.Key_Down and self._prompt_history_index >= 0:
                if self._navigate_prompt_history(-1):
                    return True
            if self._prompt_suggestions_visible and event.key() == Qt.Key.Key_Up:
                self.prompt_suggestions.setCurrentRow(max(0, self.prompt_suggestions.currentRow() - 1))
                return True
            if self._prompt_suggestions_visible and event.key() == Qt.Key.Key_Down:
                self.prompt_suggestions.setCurrentRow(
                    min(self.prompt_suggestions.count() - 1, self.prompt_suggestions.currentRow() + 1)
                )
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._prompt_suggestions_visible:
                if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self._select_prompt_suggestion(self.prompt_suggestions.currentItem())
                    return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.send()
                    return True
        if watched is search_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._hide_history_search()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._focus_next_search_match()
                return True
            if event.key() == Qt.Key.Key_Up:
                self._focus_search_match(-1)
                return True
            if event.key() == Qt.Key.Key_Down:
                self._focus_search_match(1)
                return True
        return super().eventFilter(watched, event)

    def _toggle_history_search(self):
        if self.history_search_bar.isVisible():
            self._hide_history_search()
            return
        self.history_search_bar.show()
        self.history_search_input.selectAll()
        self.history_search_input.setFocus()

    def _hide_history_search(self):
        self.history_search_bar.hide()
        self.history_search_input.clear()
        self._clear_search_highlights()
        self.input.setFocus()

    def _clear_search_highlights(self):
        for bubble in self._search_match_bubbles:
            if not sip.isdeleted(bubble):
                role, text, is_html = self._bubble_texts.get(bubble, ("assistant", "", False))
                bubble.setText(text if is_html else _to_rich(text))
                bubble.setStyleSheet(
                    "background:#89b4fa; color:#1e1e2e; border-radius:12px; padding:10px 14px;"
                    if self._bubble_texts.get(bubble, ("assistant",))[0] == "user"
                    else "background:#313244; color:#cdd6f4; border-radius:12px; padding:10px 14px;"
                )
        self._search_match_bubbles = []
        self._search_matches = []
        self._search_match_index = -1

    def _search_history(self):
        query = self.history_search_input.text().strip().casefold()
        self._clear_search_highlights()
        if not query:
            return
        self._search_match_bubbles = [
            bubble for bubble, value in self._bubble_texts.items()
            if query in value[1].casefold()
        ]
        self._search_matches = []
        for bubble in self._search_match_bubbles:
            text = self._bubble_texts[bubble][1]
            self._search_matches.extend(
                (bubble, occurrence)
                for occurrence, _ in enumerate(
                    re.finditer(re.escape(query), text, re.IGNORECASE)
                )
            )
        if self._search_matches:
            self._render_search_highlights()
            self._focus_search_match(1)

    def _focus_next_search_match(self):
        self._focus_search_match(1)

    def _focus_search_match(self, step: int):
        if not self._search_matches:
            self._search_history()
            return
        self._search_match_index = (self._search_match_index + step) % len(self._search_matches)
        self._render_search_highlights()
        target = self._search_matches[self._search_match_index][0]
        self.scroll_area.ensureWidgetVisible(target.parentWidget())

    def _render_search_highlights(self):
        query = self.history_search_input.text().strip()
        if not query:
            return
        active = self._search_matches[self._search_match_index] if self._search_match_index >= 0 else None
        for bubble in self._search_match_bubbles:
            if not sip.isdeleted(bubble):
                role, text, is_html = self._bubble_texts.get(bubble, ("assistant", "", False))
                if is_html:
                    rendered = html.escape(text)
                else:
                    rendered = html.escape(text)
                pattern = re.compile(re.escape(query), re.IGNORECASE)
                occurrence = 0

                def replace(match):
                    nonlocal occurrence
                    current = (bubble, occurrence)
                    active_style = "background:#fab387; color:#1e1e2e;"
                    other_style = "background:#f9e2af; color:#1e1e2e;"
                    style = active_style if active == current else other_style
                    replacement = f'<span style="{style} border-radius:3px;">{match.group(0)}</span>'
                    occurrence += 1
                    return replacement

                bubble.setText(pattern.sub(replace, rendered))

    def _position_prompt_suggestions(self):
        if not self._prompt_suggestions_visible:
            return
        position = self.input.mapToGlobal(QPoint(0, self.input.height() + 2))
        self.prompt_suggestions.setFixedWidth(self.input.width())
        self.prompt_suggestions.move(position)

    def _cursor_is_at_start(self) -> bool:
        cursor = self.input.textCursor()
        if cursor.hasSelection():
            return False
        text = self.input.toPlainText()
        if "\n" not in text and "\r" not in text:
            return True
        return cursor.position() == 0

    def _load_prompt_history(self):
        self._prompt_history = self.history.recent_prompts(self.history_retention)

    def _navigate_prompt_history(self, direction: int) -> bool:
        if not self._prompt_history:
            self._load_prompt_history()
        if not self._prompt_history:
            return False
        if self._prompt_history_index < 0:
            self._prompt_history_draft = self.input.toPlainText()
            self._prompt_history_index = 0
        else:
            if direction < 0 and self._prompt_history_index == 0:
                self.input.setPlainText(self._prompt_history_draft)
                self._prompt_history_index = -1
                return True
            self._prompt_history_index = max(
                0, min(len(self._prompt_history) - 1, self._prompt_history_index + direction)
            )
        self.input.setPlainText(self._prompt_history[self._prompt_history_index])
        self.input.moveCursor(self.input.textCursor().MoveOperation.End)
        return True

    def _update_prompt_suggestions(self):
        if self._prompt_history_index >= 0:
            return
        query = self.input.toPlainText().strip()
        if len(query) < 2:
            self.prompt_suggestions.hide()
            self._prompt_suggestions_visible = False
            return
        candidates = [prompt for prompt in self._prompt_history if query.casefold() in prompt.casefold()][:8]
        self.prompt_suggestions.clear()
        self.prompt_suggestions.addItems(candidates)
        self._prompt_suggestions_visible = bool(candidates)
        self.prompt_suggestions.setVisible(bool(candidates))
        if candidates:
            self.prompt_suggestions.setCurrentRow(0)
            self._position_prompt_suggestions()

    def _select_prompt_suggestion(self, item):
        if item is None:
            return
        self.input.setPlainText(item.text())
        self.input.moveCursor(self.input.textCursor().MoveOperation.End)
        self.prompt_suggestions.hide()
        self._prompt_suggestions_visible = False
        self._prompt_history_index = -1

    def send(self):
        text = self.input.toPlainText().strip()
        rich_text = self.input.toHtml()
        if not text or self.thread and self.thread.isRunning():
            return
        self.input.clear()
        self._pending_focus_widget = None
        self._focus_scheduled = False
        self._focus_generation += 1
        self._prompt_history_index = -1
        self._prompt_history_draft = ""
        self.prompt_suggestions.hide()
        self._prompt_suggestions_visible = False
        self.last_user_text = text
        self._add_bubble("user", text, is_html=False, focus=False)
        QTimer.singleShot(100, self._scroll_to_bottom)
        self._prompt_history = [text] + [prompt for prompt in self._prompt_history if prompt != text]
        QTimer.singleShot(0, lambda: self._continue_send(text, rich_text))

    def _continue_send(self, text: str, rich_text: str):
        plugin_actions = (
            "\n".join(self.plugin_manager.build_prompt_lines())
            if self.plugin_manager else ""
        )
        prompt = SYSTEM_PROMPT_TEMPLATE.format(
            plugin_actions=plugin_actions,
            now=datetime.now().astimezone().isoformat(),
        ) + self._history_prompt(text)
        self.routing_thread = RoutingThread(text, rich_text, prompt)
        self.routing_thread.route_llm.connect(self._on_route_llm)
        self.routing_thread.route_history_clear.connect(lambda: self._clear_history(confirm=True))
        self.routing_thread.start()

    def _on_route_llm(self, prompt: str):
        self.routing_thread = None
        self.send_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._is_streaming = True
        self.thinking_bubble = self._add_bubble("assistant", "・", persist=False, focus=False)
        self.thinking_bubble.setMinimumWidth(min(self._bubble_max_width(), 320))
        self.thinking_frame = 0
        self.thinking_timer.start()
        self.thread = LlmThread(self.llm, prompt)
        self.thread.chunk_received.connect(self._on_llm_chunk)
        self.thread.completed.connect(self.handle_response)
        self.thread.cancelled.connect(self._on_llm_cancelled)
        self.thread.start()

    def cancel_current_operation(self):
        cancelled = False
        for thread in (self.routing_thread, self.thread, self.display_thread, self.tech_news_search_thread, self.plugin_thread):
            if thread and thread.isRunning():
                thread.requestInterruption()
                cancelled = True
        if cancelled:
            self.cancel_button.setEnabled(False)

    def _on_llm_cancelled(self):
        self._is_streaming = False
        self.thinking_timer.stop()
        self._remove_bubble(self.thinking_bubble)
        self.thinking_bubble = None
        if self.plugin_progress_bubble and not sip.isdeleted(self.plugin_progress_bubble):
            self.plugin_progress_lines.append("キャンセル: 処理を中断しました。")
            self.plugin_progress_bubble.setText(_to_rich("\n".join(self.plugin_progress_lines)))
        self.plugin_progress_bubble = None
        self.plugin_progress_lines = []
        self._streaming_response = ""
        self._display_pending = False
        self.thread = None
        self.display_thread = None
        self.plugin_thread = None
        self._add_bubble("assistant", "処理をキャンセルしました。")
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _on_llm_chunk(self, chunk: str):
        if not self.thinking_bubble:
            return
        self.thinking_timer.stop()
        self._streaming_response += chunk
        # JSONコマンド（{}括弧内）は表示を抑制。JSON後のプレーンテキストのみ表示
        display_text = self._extract_displayable_text(self._streaming_response)
        self.thinking_bubble.setText(_to_rich(display_text) if display_text else "・")

    def _extract_displayable_text(self, response: str) -> str:
        response = response.strip()
        if not response:
            return ""
        # JSONコマンドで始まる場合は、最終的な}の後のテキストのみ抽出
        if response.startswith("{"):
            last_brace = response.rfind("}")
            if last_brace >= 0:
                after_json = response[last_brace + 1:].strip()
                return after_json
            return ""
        return response

    def handle_response(self, response: str):
        self._is_streaming = False
        self.thinking_timer.stop()
        self._remove_bubble(self.thinking_bubble)
        self.thinking_bubble = None
        self._streaming_response = ""
        command = self._parse_json(response)
        try:
            if not command:
                self._add_bubble("assistant", response)
            elif command.get("action") == "chat":
                self._add_bubble("assistant", command.get("reply", ""))
            elif command.get("action") == "chat_history_delete":
                self._clear_history(confirm=True)
            elif command.get("action") == "model_switch":
                self._switch_model(command)
            else:
                self._route_plugin_action(command)
        finally:
            if not self._display_pending:
                self.send_button.setEnabled(True)
                self.cancel_button.setEnabled(False)

    def _route_plugin_action(self, command: dict):
        """プラグインマネージャー経由でアクションをルーティングする。"""
        action_name = command.get("action", "")
        if not self.plugin_manager:
            self._add_bubble("assistant", "操作を解釈できませんでした。")
            return
        found = self.plugin_manager.find(action_name)
        if not found:
            self._add_bubble("assistant", "操作を解釈できませんでした。")
            return
        plugin, action_def = found
        if action_def.handler.startswith("builtin:"):
            self._call_builtin(action_def.handler[len("builtin:"):], command)
        else:
            self._run_plugin_action_async(plugin, action_def, command)

    def _run_plugin_action_async(self, plugin, action_def: PluginAction, command: dict):
        """CLI 実行をバックグラウンドで行い、LLM 待ちで UI を止めないようにする。"""
        self._display_pending = True
        self._is_streaming = True
        if action_def.display == "progress":
            self.plugin_progress_lines = ["処理を開始しています..."]
            self.plugin_progress_bubble = self._add_bubble(
                "assistant", self.plugin_progress_lines[0], persist=False, focus=False
            )
        else:
            self.thinking_bubble = self._add_bubble("assistant", "・", persist=False, focus=False)
            self.thinking_bubble.setMinimumWidth(min(self._bubble_max_width(), 320))
            self.thinking_frame = 0
            self.thinking_timer.start()
        self.plugin_thread = PluginActionThread(self.plugin_manager, plugin, action_def, command, self.llm)
        if action_def.display == "progress":
            self.plugin_thread.progress.connect(self._on_plugin_progress)
        self.plugin_thread.confirm_requested.connect(self._on_plugin_confirm_requested)
        self.plugin_thread.completed.connect(lambda result: self._on_plugin_completed(result, plugin, action_def))
        self.plugin_thread.cancelled.connect(self._on_llm_cancelled)
        self.cancel_button.setEnabled(True)
        self.plugin_thread.start()

    def _on_plugin_confirm_requested(self, request: ConfirmRequest):
        """プラグインCLIからの確認要求をダイアログで尋ね、回答をワーカースレッドへ返す。"""
        answer = QMessageBox.question(
            self,
            request.title or "確認",
            request.message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        request.confirmed = answer == QMessageBox.StandardButton.Yes
        request.event.set()

    def _on_plugin_progress(self, _pid: str, text: str):
        if not self.plugin_progress_bubble or sip.isdeleted(self.plugin_progress_bubble):
            return
        self.plugin_progress_lines.append(text)
        if len(self.plugin_progress_lines) > 30:
            self.plugin_progress_lines = self.plugin_progress_lines[-30:]
        self.plugin_progress_bubble.setText(_to_rich("\n".join(self.plugin_progress_lines)))
        row = self.plugin_progress_bubble.parentWidget()
        if row is not None:
            self._request_focus(row)

    def _on_plugin_completed(self, result: dict, plugin, action_def: PluginAction):
        self._is_streaming = False
        self.thinking_timer.stop()
        if action_def.display == "progress":
            if self.plugin_progress_bubble and not sip.isdeleted(self.plugin_progress_bubble):
                status = "完了" if result.get("ok", True) else f"失敗: {result.get('error', '不明なエラー')}"
                self.plugin_progress_lines.append(status)
                self.plugin_progress_bubble.setText(_to_rich("\n".join(self.plugin_progress_lines)))
            self.plugin_progress_bubble = None
            self.plugin_progress_lines = []
        else:
            if self.thinking_bubble:
                self._remove_bubble(self.thinking_bubble)
                self.thinking_bubble = None
        self._display_pending = False
        self.plugin_thread = None
        self._render_plugin_result(result, plugin, action_def)
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _call_builtin(self, method_name: str, command: dict):
        """プラグインマニフェストの builtin:<name> ハンドラを既存Pythonメソッドへ委譲する。"""
        no_arg = {
            "list_tech_news_categories": self._list_tech_news_categories,
        }
        if method_name in no_arg:
            no_arg[method_name]()
            return
        one_arg = {
            # GetNewTechInfo
            "count_tech_news": self._count_tech_news,
            "search_tech_news": self._search_tech_news,
            "add_tech_news_source": self._add_tech_news_source,
            "update_tech_news_source": self._update_tech_news_source,
            "delete_tech_news_source": self._delete_tech_news_source,
            "add_tech_news_category": self._add_tech_news_category,
            "rename_tech_news_category": self._rename_tech_news_category,
            "delete_tech_news_category": self._delete_tech_news_category,
            "mark_tech_news_read": self._mark_tech_news_read,
            "bookmark_tech_news": self._bookmark_tech_news,
            # Gmail (GmailCtrl plugin)
            "confirm_draft_create": self._confirm_draft_create,
        }
        method = one_arg.get(method_name)
        if method:
            method(command)
        else:
            self._add_bubble("assistant", f"未知の内部操作: {method_name}")

    def _render_plugin_result(self, result: dict, plugin, action: PluginAction):
        """plugin ハンドラの実行結果をマニフェストの display 設定に従って表示する。"""
        if not result.get("ok", True):
            if result.get("auth_required"):
                self._add_auth_button(plugin)
                return
            self._add_bubble("assistant", f"エラー: {result.get('error', '不明なエラー')}")
            return
        display = action.display
        if display == "progress":
            template = action.display_template
            if template:
                try:
                    text = template.format(**result)
                except (KeyError, ValueError):
                    text = "処理が完了しました。"
            else:
                text = "処理が完了しました。"
            self._add_bubble("assistant", text)
        elif display == "text":
            template = action.display_template
            if template:
                try:
                    text = template.format(**result)
                except (KeyError, ValueError):
                    text = json.dumps(result, ensure_ascii=False)
            else:
                text = json.dumps(result, ensure_ascii=False)
            self._add_bubble("assistant", text)
        elif display == "table":
            field = action.display_field
            rows = result.get(field, []) if field else []
            if not rows:
                self._add_bubble("assistant", "データがありません。")
                return
            self._render_generic_table(
                rows,
                action.display_columns,
                action.display_link_column,
                action.display_url_column,
            )
        elif display == "articles_table":
            field = action.display_field or "articles"
            articles = result.get(field, [])
            if not articles:
                self._add_bubble("assistant", "該当する記事はありません。")
                return
            self._render_articles_table(articles)
        else:
            self._add_bubble("assistant", json.dumps(result, ensure_ascii=False))

    def _render_generic_table(
        self,
        rows: list,
        columns: list[str] | None = None,
        link_column: str | None = None,
        url_column: str | None = None,
    ):
        """汎用HTMLテーブルを生成してバブル表示する。"""
        if not rows:
            self._add_bubble("assistant", "データがありません。")
            return
        if not columns:
            columns = list(rows[0].keys()) if rows else []
        th = 'style="text-align:left; padding:4px 6px; color:#a6adc8; border-bottom:2px solid #6c7086;"'
        td = 'style="padding:4px 6px; vertical-align:top; border-bottom:1px solid #45475a;"'
        header = "".join(f"<th {th}>{html.escape(str(col))}</th>" for col in columns)
        table_rows = []
        for row in rows:
            cells = []
            for col in columns:
                value = row.get(col)
                cell_text = html.escape(str(value)) if value is not None else ""
                if col == link_column and url_column and row.get(url_column):
                    url = html.escape(str(row[url_column]))
                    cell_text = f'<a href="{url}" style="color:#89dceb;">{cell_text}</a>'
                elif col == url_column and value:
                    cell_text = f'<a href="{cell_text}" style="color:#89dceb;">{cell_text}</a>'
                cells.append(f"<td {td}>{cell_text}</td>")
            table_rows.append(f"<tr>{''.join(cells)}</tr>")
        table_html = (
            f'<table style="border-collapse:collapse; width:100%;">'
            f"<tr>{header}</tr>"
            + "".join(table_rows)
            + "</table>"
        )
        self._add_bubble("assistant", table_html, is_html=True)

    def _history_prompt(self, current_text: str) -> str:
        if self.history_llm_turns <= 0:
            return f"\n今回の依頼: {current_text}"
        messages = self.history.latest(self.history_llm_turns * 2 + 1)
        if messages and messages[-1]["role"] == "user" and messages[-1]["content"] == current_text:
            messages = messages[:-1]
        # Assistant replies are left out: they can repeat earlier refusals and push the router to skip plugins.
        user_messages = [m["content"] for m in messages if m["role"] == "user"][-self.history_llm_turns:]
        lines = ["\n過去のユーザーの依頼（参考情報。今回の依頼を優先）:"]
        for content in user_messages:
            lines.append(f"ユーザー: {content}")
        lines.append(f"今回の依頼: {current_text}")
        return "\n".join(lines)

    def _clear_history(self, confirm: bool = False):
        if confirm:
            answer = QMessageBox.question(
                self,
                "会話履歴を削除",
                "保存されている会話履歴をすべて削除しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._add_bubble("assistant", "会話履歴の削除をキャンセルしました。", persist=False)
                return
        self.history.clear()
        while self.message_layout.count():
            item = self.message_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._all_bubbles.clear()
        self._bubble_texts.clear()
        self._search_match_bubbles.clear()
        self._search_match_index = -1
        self._history_oldest_id = None
        self._history_exhausted = True
        self._add_bubble("assistant", "会話履歴を削除しました。", persist=False)

    def _format_result_with_llm(self, data: dict, user_request: str):
        """取得したデータをLLMに渡して表示テキストを生成させる。"""
        fmt_prompt = (
            "以下のJSONデータをユーザーの依頼に合わせて日本語で表示してください。\n"
            "JSONではなく読みやすいテキストで答えてください。\n"
            "URLはそのまま出力してください（HTMLタグ不要）。\n"
            f"依頼: {user_request}\n"
            f"データ:\n{json.dumps(data, ensure_ascii=False, indent=2)}"
        )
        self._display_pending = True
        self._streaming_response = ""
        self._is_streaming = True
        self.thinking_bubble = self._add_bubble("assistant", "・", persist=False, focus=False)
        self.thinking_bubble.setMinimumWidth(min(self._bubble_max_width(), 320))
        self.thinking_frame = 0
        self.thinking_timer.start()
        self.display_thread = LlmThread(self.llm, fmt_prompt)
        self.display_thread.chunk_received.connect(self._on_llm_chunk)
        self.display_thread.completed.connect(self._on_format_completed)
        self.display_thread.cancelled.connect(self._on_llm_cancelled)
        self.cancel_button.setEnabled(True)
        self.display_thread.start()

    def _on_format_completed(self, response: str):
        self._is_streaming = False
        self.thinking_timer.stop()
        if self.thinking_bubble:
            self._remove_bubble(self.thinking_bubble)
            self.thinking_bubble = None
        self._display_pending = False
        self.display_thread = None
        self._add_bubble("assistant", response)
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _render_articles_table(self, articles: list):
        """記事リストをソース別テーブルHTMLで表示する（Python固定レンダリング）。"""
        groups: dict[str, list] = {}
        for a in articles:
            groups.setdefault(a.get("source") or "不明", []).append(a)
        td = 'style="padding:4px 6px; vertical-align:top; border-bottom:1px solid #45475a;"'
        td_muted = 'style="padding:4px 6px; vertical-align:top; white-space:nowrap; color:#a6adc8; border-bottom:1px solid #45475a;"'
        th = 'style="text-align:left; padding:4px 6px; color:#a6adc8; border-bottom:2px solid #6c7086;"'
        sections = []
        for src_name, arts in groups.items():
            rows = []
            for a in arts:
                title = html.escape(a.get("title") or "無題")
                url = html.escape(a.get("url") or "")
                pub = (a.get("published_at") or "")[:10]
                title_cell = f'<a href="{url}" style="color:#89dceb;">{title}</a>' if url else title
                rows.append(f"<tr><td {td}>{title_cell}</td><td {td_muted}>{pub}</td></tr>")
            sections.append(
                f'<p style="margin:8px 0 2px; color:#cba6f7; font-weight:bold;">{html.escape(src_name)}'
                f' <span style="color:#6c7086; font-weight:normal;">({len(arts)}件)</span></p>'
                f'<table style="border-collapse:collapse; width:100%;">'
                f"<tr><th {th}>タイトル</th><th {th}>日付</th></tr>"
                + "".join(rows) + "</table>"
            )
        self._add_bubble("assistant", f'<b>技術記事（{len(articles)}件）</b>' + "".join(sections), is_html=True)
        try:
            self.tech_news.mark_read([article["id"] for article in articles if "id" in article])
        except TechNewsError:
            pass

    def _search_tech_news(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        thinking = self._add_bubble("assistant", "検索中...", persist=False, focus=False)
        self._display_pending = True
        self.send_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.tech_news_search_thread = TechNewsSearchThread(self.tech_news, command)
        self.tech_news_search_thread.completed.connect(
            lambda articles: self._on_tech_news_search_done(articles, thinking)
        )
        self.tech_news_search_thread.no_results.connect(
            lambda msg: self._on_tech_news_search_no_results(msg, thinking)
        )
        self.tech_news_search_thread.failed.connect(
            lambda err: self._on_tech_news_search_failed(err, thinking)
        )
        self.tech_news_search_thread.start()

    def _on_tech_news_search_done(self, articles: list, thinking: QLabel):
        self._remove_bubble(thinking)
        self._render_articles_table(articles)
        self.tech_news_search_thread = None
        self._display_pending = False
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _on_tech_news_search_no_results(self, msg: str, thinking: QLabel):
        self._remove_bubble(thinking)
        self._add_bubble("assistant", msg)
        self.tech_news_search_thread = None
        self._display_pending = False
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _on_tech_news_search_failed(self, err: str, thinking: QLabel):
        self._remove_bubble(thinking)
        self._add_bubble("assistant", f"エラー: {err}")
        self.tech_news_search_thread = None
        self._display_pending = False
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _match_registered_source(self, name: str) -> str | None:
        try:
            result = self.tech_news.list_sources()
        except TechNewsError:
            return None
        lowered = name.lower()
        for src in result.get("sources", []):
            src_name = src.get("name", "")
            if src_name.lower() == lowered:
                return src_name
        return None

    def _match_registered_category(self, query: str) -> dict | None:
        """検索語に含まれるカテゴリ名を取得する。"""
        if not query.strip():
            return None
        try:
            result = self.tech_news.list_categories()
        except TechNewsError:
            return None
        normalized_query = re.sub(r"[\s・、】【]", "", query).lower()
        for category in result.get("categories", []):
            name = str(category.get("name", ""))
            normalized_name = re.sub(r"[\s・、】【]", "", name).lower()
            if normalized_name and normalized_name in normalized_query:
                return category
        return None

    def _count_tech_news(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        try:
            # 件数確認はlimit=100で検索し、返ってきた件数＋「以上」で表現
            result = self.tech_news.search(
                "",
                command.get("source"),
                bool(command.get("unread", False)),
                100,
            )
            count = result.get("count", len(result.get("articles", [])))
            suffix = "件以上" if count >= 100 else "件"
            source_label = f"「{command['source']}」の" if command.get("source") else ""
            unread_label = "未読" if command.get("unread") else ""
            self._add_bubble("assistant", f"{source_label}{unread_label}記事は {count}{suffix} あります。")
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _count_tech_news_per_source(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        try:
            status = command.get("status")
            if status not in {"all", "unread", "read"}:
                status = "unread" if command.get("unread") else "all"
            result = self.tech_news.count_per_source(status)
            self._format_result_with_llm(result, self.last_user_text)
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _fetch_tech_news(self):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        try:
            result = self.tech_news.fetch()
            self._add_bubble("assistant", self._format_tech_news_fetch_result(result, "技術記事を取得しました。"))
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _fetch_tech_news_automatically(self):
        if not self.tech_news or (
            self.tech_news_fetch_thread and self.tech_news_fetch_thread.isRunning()
        ):
            return
        self.tech_news_fetch_thread = TechNewsFetchThread(self.tech_news)
        self.tech_news_fetch_thread.completed.connect(self._on_automatic_tech_news_fetched)
        self.tech_news_fetch_thread.failed.connect(self._on_automatic_tech_news_fetch_failed)
        self.tech_news_fetch_thread.start()

    def _on_automatic_tech_news_fetched(self, result: dict):
        saved = int(result.get("saved", 0))
        if saved:
            self._add_bubble(
                "assistant",
                self._format_tech_news_fetch_result(result, "技術記事を自動取得しました。"),
            )
        self.tech_news_fetch_thread = None

    def _on_automatic_tech_news_fetch_failed(self, message: str):
        self.tech_news_fetch_thread = None

    @staticmethod
    def _format_tech_news_fetch_result(result: dict, heading: str) -> str:
        sources = result.get("sources")
        if not isinstance(sources, list):
            return f"{heading}\n新規追加: {result.get('saved', 0)}件"
        lines = [heading]
        for source in sources:
            count = int(source.get("count", 0))
            if count:
                lines.append(f"・{source.get('source', '不明')}: {count}件")
        return "\n".join(lines) if len(lines) > 1 else f"{heading}\n新着はありません。"

    def _list_tech_news_sources(self):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        try:
            result = self.tech_news.list_sources()
            if not result.get("sources"):
                self._add_bubble("assistant", "登録済みのソースはありません。")
                return
            self._format_result_with_llm(result, self.last_user_text)
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _add_tech_news_source(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        name = str(command.get("name", "")).strip()
        url = str(command.get("url", "")).strip()
        if not name or not url:
            self._add_bubble("assistant", "ソース名とURLを指定してください。")
            return
        raw_cat = command.get("category")
        category = str(raw_cat).strip() if raw_cat else None
        try:
            self.tech_news.add_source(name, url, category)
            suffix = f"（カテゴリ: {category}）" if category else ""
            self._add_bubble("assistant", f"ソース「{name}」を追加しました。{suffix}")
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _update_tech_news_source(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        name = str(command.get("name", "")).strip()
        if not name:
            self._add_bubble("assistant", "ソース名を指定してください。")
            return
        url = str(command.get("url", "")).strip() or None
        enabled = command.get("enabled")
        enabled_bool = bool(enabled) if enabled is not None else None
        raw_cat = command.get("category")
        category = str(raw_cat).strip() if raw_cat is not None else None
        try:
            self.tech_news.update_source(name, url, enabled_bool, category)
            self._add_bubble("assistant", f"ソース「{name}」を更新しました。")
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _delete_tech_news_source(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        name = str(command.get("name", "")).strip()
        if not name:
            self._add_bubble("assistant", "ソース名を指定してください。")
            return
        answer = QMessageBox.question(
            self, "ソースを削除", f"「{name}」を削除しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._add_bubble("assistant", "削除をキャンセルしました。")
            return
        try:
            self.tech_news.delete_source(name)
            self._add_bubble("assistant", f"ソース「{name}」を削除しました。")
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _list_tech_news_categories(self):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        try:
            result = self.tech_news.list_categories()
            if not result.get("categories") and not result.get("uncategorized"):
                self._add_bubble("assistant", "カテゴリが登録されていません。")
                return
            self._format_result_with_llm(result, self.last_user_text)
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _add_tech_news_category(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        name = str(command.get("name", "")).strip()
        if not name:
            self._add_bubble("assistant", "カテゴリ名を指定してください。")
            return
        try:
            self.tech_news.add_category(name)
            self._add_bubble("assistant", f"カテゴリ「{name}」を追加しました。")
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _rename_tech_news_category(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        old_name = str(command.get("old_name", "")).strip()
        new_name = str(command.get("new_name", "")).strip()
        if not old_name or not new_name:
            self._add_bubble("assistant", "変更前と変更後のカテゴリ名を指定してください。")
            return
        try:
            self.tech_news.rename_category(old_name, new_name)
            self._add_bubble("assistant", f"カテゴリ「{old_name}」を「{new_name}」に変更しました。")
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _delete_tech_news_category(self, command: dict):
        if not self.tech_news:
            self._add_bubble("assistant", "技術記事CLIの設定が未完了です。")
            return
        name = str(command.get("name", "")).strip()
        if not name:
            self._add_bubble("assistant", "カテゴリ名を指定してください。")
            return
        answer = QMessageBox.question(
            self, "カテゴリを削除",
            f"「{name}」を削除しますか？\n所属するソースは未分類になります。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._add_bubble("assistant", "削除をキャンセルしました。")
            return
        try:
            self.tech_news.delete_category(name)
            self._add_bubble("assistant", f"カテゴリ「{name}」を削除しました。")
        except TechNewsError as exc:
            self._add_bubble("assistant", f"エラー: {exc}")

    def _switch_model(self, command: dict):
        model = command.get("model", "").strip()
        if not model:
            self._add_bubble("assistant", "モデル名が指定されていません。")
            return
        self.llm.model = model
        self._add_bubble("assistant", f"モデルを {model} に切り替えました。モデルが未ダウンロードの場合は設定画面からダウンロードしてください。")

    def _mark_tech_news_read(self, command: dict):
        article_ids = command.get("article_ids", [])
        if not article_ids:
            self._add_bubble("assistant", "既読にする記事IDが指定されていません。")
            return
        found = self.plugin_manager.find("tech_news_mark_read") if self.plugin_manager else None
        if not found:
            self._add_bubble("assistant", "技術記事プラグインが見つかりません。")
            return
        plugin, _ = found
        result = self.plugin_manager.execute_raw(plugin, ["mark-read"], [str(aid) for aid in article_ids])
        if result.get("ok"):
            self._add_bubble("assistant", f"{result.get('updated', len(article_ids))}件の記事を既読にしました。")
        else:
            self._add_bubble("assistant", f"エラー: {result.get('error', '不明なエラー')}")

    def _bookmark_tech_news(self, command: dict):
        article_ids = command.get("article_ids", [])
        if not article_ids:
            self._add_bubble("assistant", "ブックマークする記事IDが指定されていません。")
            return
        found = self.plugin_manager.find("tech_news_bookmark") if self.plugin_manager else None
        if not found:
            self._add_bubble("assistant", "技術記事プラグインが見つかりません。")
            return
        plugin, _ = found
        subcommand = "unbookmark" if command.get("remove") else "bookmark"
        result = self.plugin_manager.execute_raw(plugin, [subcommand], [str(aid) for aid in article_ids])
        if result.get("ok"):
            action_text = "解除" if command.get("remove") else "追加"
            self._add_bubble("assistant", f"{result.get('updated', len(article_ids))}件のブックマークを{action_text}しました。")
        else:
            self._add_bubble("assistant", f"エラー: {result.get('error', '不明なエラー')}")

    def _add_auth_button(self, plugin):
        """認証が必要なときのボタン。プラグインが login を宣言していなければ案内文のみ出す。"""
        if not plugin.login_subcommands:
            self._add_bubble("assistant", f"{plugin.name} の認証が必要です。プラグインの設定を確認してください。")
            return
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(f"{plugin.name} の認証が必要です。")
        label.setStyleSheet("background:#313244; color:#cdd6f4; border-radius:12px; padding:10px 14px;")
        btn = QPushButton(plugin.login_label)
        btn.setStyleSheet("background:#a6e3a1; color:#1e1e2e; border-radius:8px; padding:6px 12px;")
        btn.clicked.connect(lambda _checked=False, p=plugin: self._do_plugin_login(p))
        row_layout.addWidget(label)
        row_layout.addWidget(btn)
        row_layout.addStretch()
        self.message_layout.addWidget(row)
        self._request_focus(row)

    def _do_plugin_login(self, plugin):
        result = self.plugin_manager.execute_raw(plugin, plugin.login_subcommands, [])
        if result.get("ok"):
            self._add_bubble("assistant", result.get("message", "ブラウザでログインしてください。完了後、再度お試しください。"))
        else:
            self._add_bubble("assistant", f"認証の起動に失敗しました: {result.get('error', '')}")
    def _confirm_draft_create(self, command: dict):
        to = command.get("to", "")
        subject = command.get("subject", "")
        body = command.get("body", "")
        preview = body[:200] + ("..." if len(body) > 200 else "")
        answer = QMessageBox.question(
            self, "メール下書き作成",
            f"宛先: {to}\n件名: {subject}\n\n{preview}\n\n下書きを作成しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._add_bubble("assistant", "下書き作成をキャンセルしました。")
            return
        found = self.plugin_manager.find("gmail_draft_create") if self.plugin_manager else None
        if not found:
            self._add_bubble("assistant", "Gmailプラグインが見つかりません。")
            return
        plugin, _ = found
        result = self.plugin_manager.execute_raw(
            plugin, ["draft-create"], ["--to", to, "--subject", subject, "--body", body]
        )
        if result.get("ok"):
            self._add_bubble("assistant", result.get("message", "下書きを作成しました。"))
        elif result.get("auth_required"):
            self._add_auth_button(plugin)
        else:
            self._add_bubble("assistant", f"エラー: {result.get('error', '不明なエラー')}")

    def _add_bubble(
        self,
        role: str,
        text: str,
        *,
        is_html: bool = False,
        persist: bool = True,
        at_top: bool = False,
        focus: bool = True,
    ) -> QLabel:
        row = QWidget()
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        bubble = QLabel()
        bubble.setTextFormat(Qt.TextFormat.RichText)
        bubble.setOpenExternalLinks(True)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        bubble.setText(text if is_html else _to_rich(text))
        max_width = self._bubble_max_width()
        bubble.setMaximumWidth(max_width)
        if not is_html and "\n" not in text:
            bubble.setMinimumWidth(min(max_width, self._bubble_content_width(bubble, text)))
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        if role == "user":
            bubble.setStyleSheet("background:#89b4fa; color:#1e1e2e; border-radius:12px; padding:10px 14px;")
            row_layout.addStretch()
            row_layout.addWidget(bubble)
        else:
            bubble.setStyleSheet("background:#313244; color:#cdd6f4; border-radius:12px; padding:10px 14px;")
            row_layout.addWidget(bubble)
            row_layout.addStretch()
        if at_top:
            self.message_layout.insertWidget(0, row)
        else:
            self.message_layout.addWidget(row)
        self._all_bubbles.append(bubble)
        self._bubble_texts[bubble] = (role, text, is_html)
        if persist:
            QTimer.singleShot(0, lambda: self._persist_bubble(role, text, is_html))
        if focus:
            self._request_focus(row)
        return bubble

    def _persist_bubble(self, role: str, text: str, is_html: bool = False) -> None:
        self.history.add(role, text, is_html=is_html)
        self._history_writes_since_prune += 1
        if self._history_writes_since_prune >= 100:
            self.history.prune(self.history_retention)
            self._history_writes_since_prune = 0

    def _remove_bubble(self, bubble: QLabel | None):
        if bubble is None:
            return
        if bubble in self._all_bubbles:
            self._all_bubbles.remove(bubble)
        self._bubble_texts.pop(bubble, None)
        row = bubble.parentWidget()
        if row is not None:
            if self._pending_focus_widget is row:
                self._pending_focus_widget = None
            self.message_layout.removeWidget(row)
            row.deleteLater()

    def _scroll_to_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    _SCROLL_TOP_MARGIN = 24

    def _scroll_to_widget(self, widget: QWidget):
        if widget is None or sip.isdeleted(widget):
            return
        try:
            # レイアウトを即時反映させてからスクロール値を計算する
            self.scroll_area.widget().updateGeometry()
            content = self.scroll_area.widget()
            y = max(0, widget.mapTo(content, QPoint(0, 0)).y() - self._SCROLL_TOP_MARGIN)
            bar = self.scroll_area.verticalScrollBar()
            # バーの最大値が更新済みでなければ ensureWidgetVisible にフォールバック
            if y <= bar.maximum():
                bar.setValue(y)
            else:
                self.scroll_area.ensureWidgetVisible(widget, 0, self._SCROLL_TOP_MARGIN)
        except RuntimeError:
            # deleteLater() 済みのウィジェットがタイマー実行前に破棄された場合は無視する。
            return

    def _request_focus(self, widget: QWidget):
        self._pending_focus_widget = widget
        if self._focus_scheduled:
            return
        self._focus_scheduled = True
        generation = self._focus_generation
        # 100ms 待ってレイアウト確定後にスクロール
        QTimer.singleShot(100, lambda: self._focus_pending_widget(generation))

    def _focus_pending_widget(self, generation: int):
        if generation != self._focus_generation:
            return
        widget = self._pending_focus_widget
        self._pending_focus_widget = None
        self._focus_scheduled = False
        self._scroll_to_widget(widget)

    def _animate_thinking(self):
        self.thinking_frame = (self.thinking_frame + 1) % 4
        self.thinking_bubble.setText("・" * (self.thinking_frame + 1))

    @staticmethod
    def _parse_json(response: str) -> dict | None:
        cleaned = response.strip().replace("```json", "").replace("```", "").strip()
        decoder = json.JSONDecoder()
        commands = []
        position = 0
        while position < len(cleaned):
            match = re.search(r"\{", cleaned[position:])
            if not match:
                break
            try:
                value, end_position = decoder.raw_decode(cleaned, position + match.start())
            except json.JSONDecodeError:
                break
            if isinstance(value, dict):
                commands.append(value)
            position = end_position
        if not commands:
            return None
        if len(commands) == 1:
            return commands[0]
        if all(command.get("action") == "tech_news_search" for command in commands):
            sources = [
                command["source"] if command.get("source") else command.get("query", "")
                for command in commands
            ]
            return {
                "action": "tech_news_search",
                "sources": sources,
                "unread": any(bool(command.get("unread", False)) for command in commands),
                "limit": commands[0].get("limit", 10),
            }
        return commands[0]
