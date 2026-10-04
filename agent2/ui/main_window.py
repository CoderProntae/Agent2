"""Dark, multi-pane PySide6 workspace for the local Agent2 coding agent."""

from __future__ import annotations

import difflib
import html
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRegularExpression, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent2.core.agent import AgentService
from agent2.core.config import AppConfig, SecretStore
from agent2.core.git_service import GitService, GitResult
from agent2.core.ollama import OllamaClient
from agent2.core.sessions import SessionStore
from agent2.core.terminal import CommandRunner, CommandResult
from agent2.core.usage import UsageStore
from agent2.core.workspace import ChangeRecord, WorkspaceError, WorkspaceService
from .dialogs import SettingsDialog
from .workers import AgentWorker, GitSyncWorker, ManualCommandWorker

logger = logging.getLogger(__name__)


APP_STYLE = """
QMainWindow, QWidget { background: #101319; color: #e6eaf0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
QFrame#topbar { background: #151a22; border-bottom: 1px solid #293140; }
QFrame#pane { background: #141922; border: 1px solid #252d39; border-radius: 10px; }
QFrame#subPane { background: #11161e; border: 1px solid #242c37; border-radius: 8px; }
QLabel#brand { color: #f0f4fa; font-size: 18px; font-weight: 700; }
QLabel#muted, QLabel#workspacePath, QLabel#subtle { color: #8d98a8; }
QLabel#sectionTitle { color: #c8d1de; font-weight: 700; font-size: 12px; letter-spacing: 1px; }
QLabel#statusPill { background: #202835; color: #c5d0df; border: 1px solid #303b4c; border-radius: 12px; padding: 5px 10px; }
QPushButton, QToolButton { background: #202735; color: #e5ebf4; border: 1px solid #333e4f; border-radius: 7px; padding: 7px 11px; }
QPushButton:hover, QToolButton:hover { background: #293547; border-color: #52627a; }
QPushButton:pressed, QToolButton:pressed { background: #34445a; }
QPushButton:disabled { background: #171d26; color: #697485; border-color: #252c36; }
QPushButton#primary { background: #3566d6; border-color: #4778e6; color: white; font-weight: 700; }
QPushButton#primary:hover { background: #4277eb; }
QPushButton#danger { color: #ff9b9b; border-color: #60343b; }
QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser, QTreeWidget, QListWidget { background: #0d1117; color: #dce3ed; border: 1px solid #2a3340; border-radius: 7px; selection-background-color: #304f85; padding: 7px; }
QPlainTextEdit, QTextBrowser { font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 12px; }
QTextBrowser { font-family: 'Segoe UI', sans-serif; font-size: 13px; line-height: 1.45; padding: 14px; }
QTreeWidget, QListWidget { padding: 4px; }
QTreeWidget::item, QListWidget::item { padding: 5px 4px; border-radius: 5px; }
QListWidget#activity::item { background:#171e28; border:1px solid #252e3a; margin:2px 1px; padding:6px 8px; border-radius:6px; }
QTreeWidget::item:hover, QListWidget::item:hover { background: #1c2532; }
QTreeWidget::item:selected, QListWidget::item:selected { background: #233757; color: #f5f8fc; }
QTabWidget::pane { border: 1px solid #28313d; border-radius: 8px; top: -1px; }
QTabBar::tab { background: #151a22; color: #8f9bab; padding: 8px 13px; border: 1px solid #242c37; border-top-left-radius: 7px; border-top-right-radius: 7px; }
QTabBar::tab:selected { color: #e7edf6; background: #202734; border-bottom-color: #202734; }
QProgressBar { background: #0c1016; color: #dce3ed; border: 1px solid #2a3340; border-radius: 5px; text-align: center; height: 14px; }
QProgressBar::chunk { background: #3f71d2; border-radius: 4px; }
QSplitter::handle { background: #202733; }
QScrollBar:vertical { width: 10px; background: #11161d; margin: 2px; }
QScrollBar::handle:vertical { background: #354151; border-radius: 5px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QStatusBar { background: #141922; color: #93a0b1; border-top: 1px solid #293140; }
"""


class CodeHighlighter(QSyntaxHighlighter):
    """Small dependency-free syntax highlighter for common source formats."""

    def __init__(self, document) -> None:
        super().__init__(document)
        self.rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._add(r"\b(class|def|return|if|elif|else|for|while|in|try|except|finally|with|as|import|from|pass|break|continue|lambda|yield|raise|async|await|None|True|False|and|or|not|is|self|const|let|var|function|new|this|export|default|public|private|static|void|int|string|bool)\b", "#c792ea", bold=True)
        self._add(r"\b(print|len|range|enumerate|map|filter|open|super|__init__)\b(?=\s*\()", "#82aaff")
        self._add(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\s*\()", "#82aaff")
        self._add(r"\b\d+(?:\.\d+)?\b", "#f78c6c")
        self._add(r"\b[A-Z][A-Z0-9_]{2,}\b", "#ffcb6b")
        self._add(r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)", "#c3e88d")
        self._add(r"(?:#[^\n]*|//[^\n]*|/\*[\s\S]*?\*/)", "#697586", italic=True)

    def _add(self, pattern: str, color: str, *, bold: bool = False, italic: bool = False) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        if italic:
            fmt.setFontItalic(True)
        self.rules.append((QRegularExpression(pattern), fmt))

    def highlightBlock(self, text: str) -> None:
        for expression, formatting in self.rules:
            iterator = expression.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), formatting)


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, document, added: bool) -> None:
        super().__init__(document)
        self.added = added
        self.formatting = QTextCharFormat()
        self.formatting.setBackground(QColor("#173528" if added else "#48272e"))
        self.formatting.setForeground(QColor("#a6e3bb" if added else "#ffb4b4"))

    def highlightBlock(self, text: str) -> None:
        if text.startswith("+") or text.startswith("-") or text.startswith("@@"):
            if (self.added and text.startswith("+")) or (not self.added and text.startswith("-")):
                self.setFormat(0, len(text), self.formatting)


class SendTextEdit(QPlainTextEdit):
    send_requested = Signal()  # type: ignore[name-defined]

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Agent2 · Yerel Yapay Zeka Çalışma Alanı")
        self.resize(1580, 980)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet(APP_STYLE)

        self.config = AppConfig.load()
        self.usage = UsageStore()
        self.sessions = SessionStore()
        self.workspace = WorkspaceService()
        if self.config.workspace:
            try:
                self.workspace.set_root(self.config.workspace)
            except (OSError, WorkspaceError):
                logger.warning("Kayıtlı çalışma alanı açılamadı: %s", self.config.workspace)
        self.terminal = CommandRunner(self.workspace)
        self.git = GitService(self.workspace)
        self.session_id = ""
        self.agent_worker: AgentWorker | None = None
        self.manual_worker: ManualCommandWorker | None = None
        self.sync_worker: GitSyncWorker | None = None
        self._streaming_text = ""
        self._run_error = ""
        self._close_when_workers_finish = False
        self._active_path = ""
        self._loaded_file_content = ""
        self._suppress_editor_change = False
        self._chat_render_timer = QTimer(self)
        self._chat_render_timer.setSingleShot(True)
        self._chat_render_timer.setInterval(80)
        self._chat_render_timer.timeout.connect(self._render_chat)

        self._build_ui()
        self._choose_initial_session()
        self._refresh_workspace_tree()
        self._update_usage()
        self._update_git_status()
        self._usage_timer = QTimer(self)
        self._usage_timer.setInterval(10_000)
        self._usage_timer.timeout.connect(self._update_usage)
        self._usage_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("topbar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        self.brand = QLabel("◈  Agent2")
        self.brand.setObjectName("brand")
        header_layout.addWidget(self.brand)
        self.workspace_label = QLabel("Çalışma alanı seçilmedi")
        self.workspace_label.setObjectName("workspacePath")
        header_layout.addWidget(self.workspace_label, 1)
        self.model_pill = QLabel("Ollama · localhost:11435")
        self.model_pill.setObjectName("statusPill")
        header_layout.addWidget(self.model_pill)
        self.open_button = QPushButton("Klasör aç")
        self.open_button.clicked.connect(self._select_workspace)
        header_layout.addWidget(self.open_button)
        self.git_status_button = QPushButton("Git durumu")
        self.git_status_button.clicked.connect(self._show_git_status)
        header_layout.addWidget(self.git_status_button)
        self.github_button = QToolButton()
        self.github_button.setText("GitHub ▾")
        self.github_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        github_menu = QMenu(self.github_button)
        github_menu.addAction("Uzak depodan al · Pull", lambda: self._start_github_sync("pull"))
        github_menu.addAction("Uzak depoya gönder · Push", lambda: self._start_github_sync("push"))
        self.github_button.setMenu(github_menu)
        header_layout.addWidget(self.github_button)
        self.settings_button = QPushButton("Ayarlar")
        self.settings_button.clicked.connect(self._open_settings)
        header_layout.addWidget(self.settings_button)
        self.admin_button = QPushButton("Kotalar")
        self.admin_button.clicked.connect(self._open_usage_editor)
        header_layout.addWidget(self.admin_button)
        layout.addWidget(header)

        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.setChildrenCollapsible(False)
        self.horizontal = QSplitter(Qt.Orientation.Horizontal)
        self.horizontal.setChildrenCollapsible(False)
        self.horizontal.addWidget(self._build_sidebar())
        self.horizontal.addWidget(self._build_chat_pane())
        self.horizontal.addWidget(self._build_editor_pane())
        self.horizontal.setSizes([260, 760, 500])
        vertical.addWidget(self.horizontal)
        vertical.addWidget(self._build_terminal_pane())
        vertical.setSizes([690, 250])
        layout.addWidget(vertical, 1)
        self.statusBar().showMessage("Yerel çalışma alanı hazır. Değişiklik ve komutlar için ajan onay ister.")
        self._sync_header()

    def _build_sidebar(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("pane")
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel("ÇALIŞMA ALANI")
        title.setObjectName("sectionTitle")
        heading.addWidget(title, 1)
        new_file = QPushButton("＋ Dosya")
        new_file.setToolTip("Çalışma alanında yeni metin dosyası oluştur")
        new_file.clicked.connect(self._create_file)
        heading.addWidget(new_file)
        layout.addLayout(heading)
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.itemDoubleClicked.connect(self._tree_item_activated)
        layout.addWidget(self.file_tree, 5)

        session_heading = QHBoxLayout()
        session_title = QLabel("SOHBETLER")
        session_title.setObjectName("sectionTitle")
        session_heading.addWidget(session_title, 1)
        new_chat = QPushButton("＋")
        new_chat.setFixedWidth(34)
        new_chat.setToolTip("Yeni sohbet")
        new_chat.clicked.connect(self._new_session)
        session_heading.addWidget(new_chat)
        layout.addLayout(session_heading)
        self.session_list = QListWidget()
        self.session_list.setMaximumHeight(170)
        self.session_list.itemClicked.connect(self._session_selected)
        layout.addWidget(self.session_list, 2)

        usage_frame = QFrame()
        usage_frame.setObjectName("subPane")
        usage_layout = QVBoxLayout(usage_frame)
        usage_layout.setContentsMargins(10, 9, 10, 9)
        usage_layout.setSpacing(5)
        usage_header = QHBoxLayout()
        usage_title = QLabel("KULLANIM")
        usage_title.setObjectName("sectionTitle")
        usage_header.addWidget(usage_title, 1)
        self.policy_label = QLabel("Aktif")
        self.policy_label.setObjectName("subtle")
        usage_header.addWidget(self.policy_label)
        usage_layout.addLayout(usage_header)
        self.tokens_label = QLabel("Token: 0 / 500k")
        self.requests_label = QLabel("İstek: 0 / 500")
        self.executions_label = QLabel("Yürütme: 0 / 200")
        for label in (self.tokens_label, self.requests_label, self.executions_label):
            label.setObjectName("muted")
            usage_layout.addWidget(label)
        self.tokens_bar = QProgressBar()
        self.tokens_bar.setRange(0, 1000)
        self.tokens_bar.setValue(0)
        self.tokens_bar.setTextVisible(False)
        usage_layout.addWidget(self.tokens_bar)
        layout.addWidget(usage_frame)
        return pane

    def _build_chat_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("pane")
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(9)
        heading = QHBoxLayout()
        title = QLabel("Yerel ajan")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        heading.addWidget(title)
        heading.addStretch(1)
        self.chat_state = QLabel("Hazır")
        self.chat_state.setObjectName("muted")
        heading.addWidget(self.chat_state)
        layout.addLayout(heading)
        # Use a rich-text browser for Markdown rendering; deltas are throttled while streaming.
        self.chat_view = QTextBrowser()
        self.chat_view.setReadOnly(True)
        self.chat_view.setOpenExternalLinks(False)
        self.chat_view.setOpenLinks(False)
        self.chat_view.anchorClicked.connect(self._open_safe_link)
        layout.addWidget(self.chat_view, 5)

        activity_title = QLabel("CANLI EYLEMLER")
        activity_title.setObjectName("sectionTitle")
        layout.addWidget(activity_title)
        self.activity_list = QListWidget()
        self.activity_list.setObjectName("activity")
        self.activity_list.setMaximumHeight(125)
        self.activity_list.setMinimumHeight(80)
        layout.addWidget(self.activity_list)

        self.prompt_input = SendTextEdit()
        self.prompt_input.setPlaceholderText("Görevi yazın…  (Ctrl + Enter ile gönder)")
        self.prompt_input.setMaximumHeight(150)
        self.prompt_input.send_requested.connect(self._send_message)
        layout.addWidget(self.prompt_input)
        bottom = QHBoxLayout()
        hint = QLabel("Dosya yazma, Git ve terminal işlemlerinde her zaman onayınız alınır.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        bottom.addWidget(hint, 1)
        self.stop_button = QPushButton("Durdur")
        self.stop_button.setObjectName("danger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_agent)
        bottom.addWidget(self.stop_button)
        self.send_button = QPushButton("Gönder  ↑")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self._send_message)
        bottom.addWidget(self.send_button)
        layout.addLayout(bottom)
        return pane

    def _build_editor_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("pane")
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel("DOSYA / FARK")
        title.setObjectName("sectionTitle")
        heading.addWidget(title, 1)
        self.file_label = QLabel("Dosya seçilmedi")
        self.file_label.setObjectName("muted")
        self.file_label.setMaximumWidth(220)
        heading.addWidget(self.file_label)
        self.save_file_button = QPushButton("Kaydet")
        self.save_file_button.setEnabled(False)
        self.save_file_button.clicked.connect(self._save_current_file)
        heading.addWidget(self.save_file_button)
        layout.addLayout(heading)
        self.editor_tabs = QTabWidget()
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._code_highlighter: CodeHighlighter | None = None
        self.editor.textChanged.connect(self._editor_changed)
        self.editor_tabs.addTab(self.editor, "Dosya")
        diff_split = QSplitter(Qt.Orientation.Horizontal)
        self.diff_before = QPlainTextEdit()
        self.diff_after = QPlainTextEdit()
        for widget in (self.diff_before, self.diff_after):
            widget.setReadOnly(True)
            widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._diff_before_highlighter = DiffHighlighter(self.diff_before.document(), False)
        self._diff_after_highlighter = DiffHighlighter(self.diff_after.document(), True)
        before_panel = QWidget()
        before_layout = QVBoxLayout(before_panel)
        before_layout.setContentsMargins(2, 2, 2, 2)
        before_layout.addWidget(QLabel("ÖNCE · SİLİNEN / ESKİ"))
        before_layout.addWidget(self.diff_before)
        after_panel = QWidget()
        after_layout = QVBoxLayout(after_panel)
        after_layout.setContentsMargins(2, 2, 2, 2)
        after_layout.addWidget(QLabel("SONRA · EKLENEN / YENİ"))
        after_layout.addWidget(self.diff_after)
        diff_split.addWidget(before_panel)
        diff_split.addWidget(after_panel)
        diff_split.setSizes([1, 1])
        self.editor_tabs.addTab(diff_split, "Yan yana fark")
        layout.addWidget(self.editor_tabs, 1)
        self.diff_summary = QLabel("Ajan değişiklikleri burada gösterilir.")
        self.diff_summary.setObjectName("muted")
        self.diff_summary.setWordWrap(True)
        layout.addWidget(self.diff_summary)
        return pane

    def _build_terminal_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("pane")
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel("TERMINAL ÇIKTISI")
        title.setObjectName("sectionTitle")
        header.addWidget(title, 1)
        safety = QLabel("Kabuk kapalı · geliştirici komutları onayla çalışır")
        safety.setObjectName("muted")
        header.addWidget(safety)
        clear_button = QPushButton("Temizle")
        clear_button.clicked.connect(lambda: self.terminal_view.clear())
        header.addWidget(clear_button)
        layout.addLayout(header)
        self.terminal_view = QPlainTextEdit()
        self.terminal_view.setReadOnly(True)
        self.terminal_view.setMaximumBlockCount(6000)
        self.terminal_view.setPlaceholderText("Ajan ve manuel komutların stdout / stderr çıktısı burada görünür.")
        layout.addWidget(self.terminal_view, 1)
        run_row = QHBoxLayout()
        self.terminal_prompt = QLineEdit()
        self.terminal_prompt.setPlaceholderText("Örn. pytest  veya  npm run build  (shell zinciri kapalı)")
        self.terminal_prompt.returnPressed.connect(self._run_manual_command)
        run_row.addWidget(self.terminal_prompt, 1)
        self.terminal_run_button = QPushButton("Komutu çalıştır")
        self.terminal_run_button.clicked.connect(self._run_manual_command)
        run_row.addWidget(self.terminal_run_button)
        self.terminal_stop_button = QPushButton("Durdur")
        self.terminal_stop_button.setObjectName("danger")
        self.terminal_stop_button.setEnabled(False)
        self.terminal_stop_button.clicked.connect(self._stop_manual_command)
        run_row.addWidget(self.terminal_stop_button)
        layout.addLayout(run_row)
        return pane

    def _choose_initial_session(self) -> None:
        sessions = self.sessions.list_sessions()
        if sessions:
            self.session_id = sessions[0].id
        else:
            self.session_id = self.sessions.new_session()
        self._refresh_sessions()
        self._render_chat()

    def _refresh_sessions(self) -> None:
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for session in self.sessions.list_sessions():
            item = QListWidgetItem(session.title or "Yeni sohbet")
            item.setData(Qt.ItemDataRole.UserRole, session.id)
            item.setToolTip(f"Güncellendi: {session.updated_at}")
            if session.id == self.session_id:
                item.setSelected(True)
            self.session_list.addItem(item)
        self.session_list.blockSignals(False)

    def _session_selected(self, item: QListWidgetItem) -> None:
        if any(worker and worker.isRunning() for worker in (self.agent_worker, self.manual_worker, self.sync_worker)):
            QMessageBox.information(self, "Çalışma alanı meşgul", "Oturum değiştirmeden önce devam eden işlemin bitmesini bekleyin.")
            self._refresh_sessions()
            return
        self.session_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._streaming_text = ""
        self._run_error = ""
        self._render_chat()
        self._update_usage()

    def _new_session(self) -> None:
        if any(worker and worker.isRunning() for worker in (self.agent_worker, self.manual_worker, self.sync_worker)):
            QMessageBox.information(self, "Çalışma alanı meşgul", "Yeni sohbet açmadan önce devam eden işlemin bitmesini bekleyin.")
            return
        self.session_id = self.sessions.new_session()
        self._streaming_text = ""
        self._run_error = ""
        self._refresh_sessions()
        self._render_chat()
        self.prompt_input.setFocus()
        self._update_usage()

    def _render_chat(self) -> None:
        if not hasattr(self, "chat_view"):
            return
        was_at_bottom = self.chat_view.verticalScrollBar().value() >= self.chat_view.verticalScrollBar().maximum() - 12
        messages = self.sessions.load(self.session_id) if self.session_id else []
        parts = ["# Agent2 yerel kodlama ajanı\n\n" ]
        if not messages:
            parts.append("Çalışma klasörünüzü açın ve görevi yazın. Ajan, varsayılan olarak `http://localhost:11435` adresindeki Ollama modelini kullanır.\n\n")
        for message in messages:
            role = message.get("role")
            content = str(message.get("content", ""))
            if role == "user":
                parts.append("### 👤 Siz\n\n" + html.escape(content) + "\n\n---\n\n")
            elif role == "assistant":
                parts.append("### ◈ Agent2\n\n" + html.escape(content) + "\n\n")
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    names = [str(call.get("function", {}).get("name", "araç")) for call in tool_calls if isinstance(call, dict)]
                    parts.append("> Araç isteği: " + ", ".join(names) + "\n\n")
                parts.append("---\n\n")
            elif role == "tool":
                title = html.escape(str(message.get("tool_name", "araç")))
                escaped = html.escape(content)
                parts.append(f"#### ⚙ Araç sonucu · `{title}`\n\n````json\n{escaped}\n````\n\n")
        if self._streaming_text:
            parts.append("### ◈ Agent2 · yazıyor…\n\n" + html.escape(self._streaming_text) + "\n")
        if self._run_error:
            parts.append("### ⚠ Hata\n\n" + html.escape(self._run_error) + "\n")
        self.chat_view.setMarkdown("".join(parts))
        if was_at_bottom:
            self.chat_view.verticalScrollBar().setValue(self.chat_view.verticalScrollBar().maximum())

    def _send_message(self) -> None:
        if self.agent_worker and self.agent_worker.isRunning():
            return
        if (self.manual_worker and self.manual_worker.isRunning()) or (self.sync_worker and self.sync_worker.isRunning()):
            QMessageBox.information(self, "Çalışma alanı meşgul", "Önce devam eden terminal veya GitHub işleminin bitmesini bekleyin.")
            return
        if not self._resolve_editor_changes():
            return
        message = self.prompt_input.toPlainText().strip()
        if not message:
            return
        if self.workspace.root is None:
            QMessageBox.information(self, "Çalışma alanı gerekli", "Önce sol üstteki 'Klasör aç' düğmesiyle yerel proje klasörünü seçin.")
            return
        policy = self.usage.get_policy()
        if policy["locked"]:
            QMessageBox.warning(self, "Ajan kilitli", "Yönetici, yerel ajan eylemlerini kilitledi. Kota aracından kilidi kaldırın.")
            return
        try:
            # Add the user turn synchronously so the UI renders it immediately. The
            # service detects this last message and does not append a duplicate.
            current = self.sessions.load(self.session_id)
            if not current or current[-1].get("role") != "user" or current[-1].get("content") != message:
                self.sessions.append(self.session_id, {"role": "user", "content": message})
        except Exception as exc:
            QMessageBox.warning(self, "Sohbet kaydedilemedi", str(exc))
            return
        self.prompt_input.clear()
        self._streaming_text = ""
        self._run_error = ""
        self._render_chat()
        client = OllamaClient(self.config.ollama_url)
        service = AgentService(
            ollama=client,
            workspace=self.workspace,
            terminal=self.terminal,
            git=self.git,
            usage=self.usage,
            sessions=self.sessions,
        )
        worker = AgentWorker(service, session_id=self.session_id, user_message=message, model=self.config.model, parent=self)
        self.agent_worker = worker
        worker.delta.connect(self._on_agent_delta)
        worker.activity.connect(self._on_activity)
        worker.terminal_output.connect(self._on_terminal_output)
        worker.change.connect(self._on_workspace_change)
        worker.approval_requested.connect(self._on_approval_requested)
        worker.completed.connect(self._on_agent_completed)
        worker.failed.connect(self._on_agent_failed)
        worker.cancelled.connect(self._on_agent_cancelled)
        worker.finished.connect(self._agent_thread_finished)
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.chat_state.setText("Çalışıyor…")
        self.statusBar().showMessage(f"{self.config.model} · {self.config.ollama_url}")
        worker.start()
        self._refresh_busy_controls()

    def _on_agent_delta(self, text: str) -> None:
        self._streaming_text += text
        if not self._chat_render_timer.isActive():
            self._chat_render_timer.start()

    def _on_activity(self, message: str) -> None:
        self._add_activity(message)
        if message.startswith("Araç çağrısı:"):
            # The completed pre-tool assistant message has already been persisted.
            self._streaming_text = ""
            self._chat_render_timer.start()
        if message.startswith("Ollama isteği gönderiliyor"):
            self.chat_state.setText("Model düşünüyor…")
        elif message.startswith("Terminal komutu çalışıyor"):
            self.chat_state.setText("Terminal çalışıyor…")
        elif message.startswith("Yanıt tamamlandı"):
            self.chat_state.setText("Tamamlandı")
        self._update_usage()

    def _add_activity(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"●  {timestamp}  {message}")
        item.setToolTip(message)
        item.setForeground(QColor("#91b5ff" if "hata" not in message.lower() else "#ff9292"))
        self.activity_list.addItem(item)
        while self.activity_list.count() > 40:
            self.activity_list.takeItem(0)
        self.activity_list.scrollToBottom()

    def _on_terminal_output(self, stream_name: str, text: str) -> None:
        cursor = self.terminal_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(f"[{stream_name}] {text}")
        self.terminal_view.setTextCursor(cursor)
        self.terminal_view.ensureCursorVisible()

    def _on_approval_requested(self, request_id: str, title: str, details: str) -> None:
        worker = self.agent_worker
        if not worker or worker.cancel_event.is_set():
            if worker:
                worker.resolve_approval(request_id, False)
            return
        box = QMessageBox(self)
        box.setWindowTitle("Ajan eylemi için onay")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Ajan şu eylemi gerçekleştirmek istiyor:\n\n<b>{html.escape(title)}</b>")
        box.setInformativeText("Devam etmeden önce aşağıdaki ayrıntıları inceleyin. Terminal programları kullanıcı hesabınızın izinlerine sahiptir; onaylamadığınız sürece çalıştırılmaz.")
        box.setDetailedText(details)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("Onayla")
        box.button(QMessageBox.StandardButton.No).setText("Reddet")
        box.setDefaultButton(QMessageBox.StandardButton.No)
        accepted = box.exec() == QMessageBox.StandardButton.Yes
        worker.resolve_approval(request_id, accepted)

    def _on_agent_completed(self, _: str) -> None:
        self._streaming_text = ""
        self._run_error = ""
        self._render_chat()
        self._refresh_sessions()
        self.chat_state.setText("Tamamlandı")
        self.statusBar().showMessage("Ajan turu tamamlandı.", 5000)
        self._update_usage()

    def _on_agent_failed(self, message: str) -> None:
        self._streaming_text = ""
        self._run_error = message
        self._render_chat()
        self._add_activity(f"Ajan hatası: {message}")
        self.chat_state.setText("Hata")
        self.statusBar().showMessage("Ajan işlemi hata verdi; günlük dosyasına ayrıntılı iz yazıldı.", 8000)
        self._update_usage()

    def _on_agent_cancelled(self) -> None:
        self._streaming_text = ""
        self._render_chat()
        self._add_activity("Ajan çalışması kullanıcı tarafından durduruldu.")
        self.chat_state.setText("Durduruldu")
        self._update_usage()

    def _agent_thread_finished(self) -> None:
        worker = self.agent_worker
        self.agent_worker = None
        if worker:
            worker.deleteLater()
        self.send_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.prompt_input.setFocus()
        self._refresh_sessions()
        self._update_usage()
        if self._close_when_workers_finish:
            self._try_deferred_close()

    def _stop_agent(self) -> None:
        if self.agent_worker and self.agent_worker.isRunning():
            self.agent_worker.stop()
            self.stop_button.setEnabled(False)
            self.chat_state.setText("Durduruluyor…")
            self.statusBar().showMessage("Geçerli yerel model isteği sonlanırken bekleniyor…")

    def _refresh_workspace_tree(self) -> None:
        self.file_tree.clear()
        root = self.workspace.root
        if root is None:
            placeholder = QTreeWidgetItem(["Klasör açarak başlayın"])
            placeholder.setDisabled(True)
            self.file_tree.addTopLevelItem(placeholder)
            self.workspace_label.setText("Çalışma alanı seçilmedi")
            return
        self.workspace_label.setText(str(root))
        root_item = QTreeWidgetItem([root.name or str(root)])
        root_item.setData(0, Qt.ItemDataRole.UserRole, ".")
        root_item.setExpanded(True)
        self.file_tree.addTopLevelItem(root_item)
        nodes: dict[str, QTreeWidgetItem] = {".": root_item}
        try:
            entries = self.workspace.list_files(".", limit=5000)
        except WorkspaceError as exc:
            self._add_activity(f"Dosya ağacı yüklenemedi: {exc}")
            return
        for entry in entries:
            path = str(entry["path"])
            parts = path.split("/")
            parent_path = "."
            for index, part in enumerate(parts):
                current_path = "/".join(parts[: index + 1])
                is_final = index == len(parts) - 1
                if current_path in nodes:
                    parent_path = current_path
                    continue
                parent = nodes.get(parent_path, root_item)
                label = part + ("/" if is_final and entry["is_dir"] else "")
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, current_path)
                item.setToolTip(0, f"{path}{'/' if entry['is_dir'] else ''}")
                if entry["is_dir"]:
                    item.setForeground(0, QColor("#84b8ff"))
                parent.addChild(item)
                nodes[current_path] = item
                parent_path = current_path
        self.file_tree.expandToDepth(1)
        self._sync_header()

    def _tree_item_activated(self, item: QTreeWidgetItem, _: int) -> None:
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if not path or path == ".":
            return
        try:
            target = self.workspace.resolve(path, allow_root=True)
            if target.is_dir():
                return
            self._open_file(path)
        except WorkspaceError as exc:
            QMessageBox.warning(self, "Dosya açılamadı", str(exc))

    def _open_file(self, path: str) -> None:
        if path != self._active_path and not self._resolve_editor_changes():
            return
        try:
            content = self.workspace.read_file(path)
        except WorkspaceError as exc:
            QMessageBox.warning(self, "Dosya açılamadı", str(exc))
            return
        self._active_path = self.workspace.relative_path(path)
        self._loaded_file_content = content
        self._suppress_editor_change = True
        self.editor.setPlainText(content)
        self._suppress_editor_change = False
        self.editor.document().setModified(False)
        self.file_label.setText(self._active_path)
        self.file_label.setToolTip(self._active_path)
        if self._code_highlighter:
            self._code_highlighter.setDocument(None)
        self._code_highlighter = CodeHighlighter(self.editor.document())
        self._update_save_button()
        self.editor_tabs.setCurrentIndex(0)

    def _editor_changed(self) -> None:
        if not self._suppress_editor_change:
            self._update_save_button()

    def _update_save_button(self) -> None:
        modified = bool(self._active_path) and self.editor.toPlainText() != self._loaded_file_content
        self.save_file_button.setEnabled(modified)
        if self._active_path:
            self.file_label.setText(self._active_path + ("  •" if modified else ""))

    def _resolve_editor_changes(self) -> bool:
        if not self._active_path or self.editor.toPlainText() == self._loaded_file_content:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Kaydedilmemiş değişiklikler")
        box.setText(f"'{self._active_path}' dosyasında kaydedilmemiş değişiklikler var.")
        box.setInformativeText("Devam etmeden önce değişiklikleri kaydedin veya atın.")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Save).setText("Kaydet")
        box.button(QMessageBox.StandardButton.Discard).setText("At")
        box.button(QMessageBox.StandardButton.Cancel).setText("İptal")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Discard:
            return True
        try:
            change = self.workspace.write_file(self._active_path, self.editor.toPlainText())
        except (WorkspaceError, OSError) as exc:
            QMessageBox.warning(self, "Dosya kaydedilemedi", str(exc))
            return False
        self._on_workspace_change(change)
        return True

    def _save_current_file(self) -> None:
        if not self._active_path:
            return
        if QMessageBox.question(self, "Dosya kaydedilsin mi?", f"'{self._active_path}' dosyasındaki değişiklikleri çalışma alanına yaz?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            change = self.workspace.write_file(self._active_path, self.editor.toPlainText())
        except WorkspaceError as exc:
            QMessageBox.warning(self, "Dosya kaydedilemedi", str(exc))
            return
        self._on_workspace_change(change)
        self._add_activity(f"Dosya kullanıcı tarafından kaydedildi: {change.path}")

    def _create_file(self) -> None:
        if any(worker and worker.isRunning() for worker in (self.agent_worker, self.manual_worker, self.sync_worker)):
            QMessageBox.information(self, "Çalışma alanı meşgul", "Dosya oluşturmak için devam eden işlemin bitmesini bekleyin.")
            return
        if self.workspace.root is None:
            self._select_workspace()
            if self.workspace.root is None:
                return
        path, accepted = QInputDialog.getText(self, "Yeni dosya", "Çalışma alanına göreli dosya yolu:")
        if not accepted or not path.strip():
            return
        try:
            target = self.workspace.resolve(path.strip())
            if target.exists():
                QMessageBox.warning(self, "Dosya zaten var", "Var olan bir dosyanın üzerine yazmak için editörde Kaydet'i kullanın.")
                return
            change = self.workspace.write_file(path.strip(), "")
            self._on_workspace_change(change)
            self._open_file(path.strip())
        except (WorkspaceError, OSError) as exc:
            QMessageBox.warning(self, "Dosya oluşturulamadı", str(exc))

    def _select_workspace(self) -> None:
        if any(worker and worker.isRunning() for worker in (self.agent_worker, self.manual_worker, self.sync_worker)):
            QMessageBox.information(self, "Çalışma alanı meşgul", "Klasör değiştirmeden önce devam eden işlemin bitmesini bekleyin.")
            return
        selected = QFileDialog.getExistingDirectory(self, "Çalışma klasörünü seçin", str(self.workspace.root or Path.home()))
        if not selected:
            return
        if self.workspace.root and Path(selected).resolve() == self.workspace.root:
            return
        if not self._resolve_editor_changes():
            return
        try:
            root = self.workspace.set_root(selected)
            self.config.workspace = str(root)
            self.config.save()
        except (WorkspaceError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Çalışma alanı açılamadı", str(exc))
            return
        self._refresh_workspace_tree()
        self._update_git_status()
        self.statusBar().showMessage(f"Çalışma alanı açıldı: {root}", 5000)

    def _update_file_diff(self, before: str, after: str) -> None:
        self.diff_before.setPlainText(before)
        self.diff_after.setPlainText(after)
        before_lines = before.splitlines()
        after_lines = after.splitlines()
        matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
        old_changed: set[int] = set()
        new_changed: set[int] = set()
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                old_changed.update(range(i1, i2))
                new_changed.update(range(j1, j2))
        self._highlight_line_numbers(self.diff_before, old_changed, QColor("#48272e"))
        self._highlight_line_numbers(self.diff_after, new_changed, QColor("#173528"))

    @staticmethod
    def _highlight_line_numbers(editor: QPlainTextEdit, lines: set[int], color: QColor) -> None:
        selections = []
        for line_number in sorted(lines):
            block = editor.document().findBlockByNumber(line_number)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(block)
            selection.format.setBackground(color)
            selections.append(selection)
        editor.setExtraSelections(selections)

    def _on_workspace_change(self, change: ChangeRecord) -> None:
        moved_from = change.previous_path
        path_summary = f"{moved_from} → {change.path}" if moved_from else change.path
        self._add_activity(f"Dosya {change.operation}: {path_summary}")
        self.diff_summary.setText(f"{change.operation}: {path_summary}")
        self._update_file_diff(change.before, change.after)
        self.editor_tabs.setCurrentIndex(1)
        self._refresh_workspace_tree()
        active_match = change.path == self._active_path or moved_from == self._active_path
        if active_match:
            if change.operation == "silindi":
                self._active_path = ""
                self.editor.clear()
                self.file_label.setText("Dosya seçilmedi")
            else:
                self._active_path = change.path
                self.file_label.setText(change.path)
                self._suppress_editor_change = True
                self.editor.setPlainText(change.after)
                self._suppress_editor_change = False
                self._loaded_file_content = change.after
                self.editor.document().setModified(False)
                self._update_save_button()
        self._update_git_status()

    def _stop_manual_command(self) -> None:
        if self.manual_worker and self.manual_worker.isRunning():
            self.manual_worker.stop()
            self.terminal_stop_button.setEnabled(False)
            self._append_terminal("\n[Komut durduruluyor…]\n")

    def _run_manual_command(self) -> None:
        if self.manual_worker and self.manual_worker.isRunning():
            return
        if (self.agent_worker and self.agent_worker.isRunning()) or (self.sync_worker and self.sync_worker.isRunning()):
            QMessageBox.information(self, "Çalışma alanı meşgul", "Önce ajan veya GitHub işleminin bitmesini bekleyin.")
            return
        command = self.terminal_prompt.text().strip()
        if not command:
            return
        if self.workspace.root is None:
            QMessageBox.warning(self, "Çalışma alanı gerekli", "Önce bir klasör açın.")
            return
        try:
            CommandRunner.parse(command)
        except ValueError as exc:
            QMessageBox.warning(self, "Komut reddedildi", str(exc))
            return
        details = (f"Komut: {command}\nÇalışma klasörü: {self.workspace.root}\n\n"
                   "Komut kabuk olmadan çalışır; yine de başlatılan programlar kullanıcı hesabınızın yetkilerine sahip olabilir.")
        if QMessageBox.question(self, "Terminal komutu onayı", details,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        worker = ManualCommandWorker(self.terminal, self.usage, self.session_id, command, self)
        self.manual_worker = worker
        worker.output.connect(self._on_terminal_output)
        worker.completed.connect(self._on_manual_command_complete)
        worker.failed.connect(lambda message: self._append_terminal(f"[hata] {message}\n"))
        worker.finished.connect(self._manual_thread_finished)
        self.terminal_run_button.setEnabled(False)
        self.terminal_prompt.setEnabled(False)
        self._append_terminal(f"\n$ {command}\n")
        worker.start()
        self.terminal_stop_button.setEnabled(True)
        self._refresh_busy_controls()

    def _on_manual_command_complete(self, result: CommandResult) -> None:
        state = "tamamlandı" if result.succeeded else f"başarısız · çıkış {result.exit_code}"
        self._append_terminal(f"\n[{state} · {result.duration_seconds:.1f} sn]\n")
        self._update_usage()

    def _manual_thread_finished(self) -> None:
        worker = self.manual_worker
        self.manual_worker = None
        if worker:
            worker.deleteLater()
        self.terminal_run_button.setEnabled(True)
        self.terminal_prompt.setEnabled(True)
        self._update_usage()
        if self._close_when_workers_finish:
            self._try_deferred_close()

    def _append_terminal(self, text: str) -> None:
        cursor = self.terminal_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.terminal_view.setTextCursor(cursor)
        self.terminal_view.ensureCursorVisible()

    def _refresh_busy_controls(self) -> None:
        agent_busy = bool(self.agent_worker and self.agent_worker.isRunning())
        manual_busy = bool(self.manual_worker and self.manual_worker.isRunning())
        sync_busy = bool(self.sync_worker and self.sync_worker.isRunning())
        busy = agent_busy or manual_busy or sync_busy
        self.open_button.setEnabled(not busy)
        self.file_tree.setEnabled(not busy)
        self.editor.setReadOnly(busy)
        self.save_file_button.setEnabled(not busy and bool(self._active_path) and self.editor.toPlainText() != self._loaded_file_content)
        self.github_button.setEnabled(not busy)
        self.git_status_button.setEnabled(not busy)
        self.terminal_prompt.setEnabled(not busy)
        self.terminal_stop_button.setEnabled(manual_busy)
        if busy:
            self.send_button.setEnabled(False)
            self.terminal_run_button.setEnabled(False)

    def _update_usage(self) -> None:
        self._refresh_busy_controls()
        try:
            snapshot, session_tokens, session_seconds = self.usage.snapshot(self.session_id or None)
            limits = self.usage.get_limits()
            policy = self.usage.get_policy()
            total_tokens = snapshot.total_tokens
            self.tokens_label.setText(f"Token: {total_tokens:,} / {limits.max_daily_tokens:,} bugün")
            self.requests_label.setText(f"İstek: {snapshot.requests:,} / {limits.max_daily_requests:,} bugün")
            self.executions_label.setText(f"Yürütme: {snapshot.executions:,} / {limits.max_daily_executions:,} bugün")
            ratio = min(1000, int(total_tokens / max(1, limits.max_daily_tokens) * 1000))
            self.tokens_bar.setValue(ratio)
            session_limit = limits.max_session_minutes * 60
            quota_exhausted = (
                snapshot.requests >= limits.max_daily_requests
                or total_tokens >= limits.max_daily_tokens
                or session_tokens >= limits.max_session_tokens
                or session_seconds >= session_limit
            )
            if policy["developer_override"]:
                quota_exhausted = False
            if policy["locked"]:
                self.policy_label.setText("Kilitli")
                self.policy_label.setStyleSheet("color:#ff9494;font-weight:700")
            elif policy["developer_override"]:
                self.policy_label.setText("Geliştirici geçersiz kılma")
                self.policy_label.setStyleSheet("color:#ffc878")
            elif quota_exhausted:
                self.policy_label.setText("Kota doldu")
                self.policy_label.setStyleSheet("color:#ff9b73;font-weight:700")
            elif snapshot.executions >= limits.max_daily_executions:
                self.policy_label.setText("Terminal kotası")
                self.policy_label.setStyleSheet("color:#ff9b73;font-weight:700")
            else:
                self.policy_label.setText("Aktif")
                self.policy_label.setStyleSheet("color:#86d4a2")
            can_send = not policy["locked"] and not quota_exhausted
            busy = bool((self.agent_worker and self.agent_worker.isRunning())
                        or (self.manual_worker and self.manual_worker.isRunning())
                        or (self.sync_worker and self.sync_worker.isRunning()))
            self.send_button.setEnabled(can_send and not busy)
            self.send_button.setToolTip("Yönetici kilidi veya kullanım kotası ajan isteklerini durdurdu." if not can_send else "")
            execution_blocked = policy["locked"] or (
                not policy["developer_override"] and snapshot.executions >= limits.max_daily_executions
            )
            terminal_busy = bool(self.manual_worker and self.manual_worker.isRunning())
            workspace_busy = bool((self.agent_worker and self.agent_worker.isRunning())
                                  or (self.sync_worker and self.sync_worker.isRunning()))
            self.terminal_run_button.setEnabled(not execution_blocked and not terminal_busy and not workspace_busy)
            self.terminal_prompt.setEnabled(not execution_blocked and not terminal_busy and not workspace_busy)
            self.github_button.setEnabled(not execution_blocked and not busy)
            self.policy_label.setToolTip(f"Bu oturum: {session_tokens:,} token · {session_seconds/60:.1f} dk")
        except Exception:
            logger.exception("Kullanım bilgisi güncellenemedi")
            self.policy_label.setText("Kota verisi hatalı")
            self.policy_label.setStyleSheet("color:#ff9494;font-weight:700")
            self.send_button.setEnabled(False)
            self.terminal_run_button.setEnabled(False)

    def _update_git_status(self) -> None:
        self._sync_header()
        if self.workspace.root is None:
            return
        result = self.git.status()
        if result.succeeded:
            first = result.stdout.splitlines()[0] if result.stdout.splitlines() else "Git"
            self.git_status_button.setText(first[:32])
            self.git_status_button.setToolTip(result.stdout.strip())
        else:
            self.git_status_button.setText("Git durumu")
            self.git_status_button.setToolTip(result.stderr.strip() or "Bu klasör henüz Git deposu değil.")

    def _sync_header(self) -> None:
        if self.workspace.root:
            self.workspace_label.setText(str(self.workspace.root))
            self.workspace_label.setToolTip(str(self.workspace.root))
        else:
            self.workspace_label.setText("Çalışma alanı seçilmedi")
        self.model_pill.setText(f"Ollama · {self.config.ollama_url.replace('http://', '').replace('https://', '')} · {self.config.model}")
        self.model_pill.setToolTip(f"{self.config.ollama_url}\nModel: {self.config.model}")

    def _show_git_status(self) -> None:
        if self.workspace.root is None:
            QMessageBox.information(self, "Git", "Önce bir çalışma klasörü açın.")
            return
        status = self.git.status()
        diff = self.git.diff()
        message = status.stdout.strip() or status.stderr.strip() or "Git deposu bulunamadı."
        details = diff.stdout.strip() or diff.stderr.strip() or "Çalışma ağacında fark yok."
        box = QMessageBox(self)
        box.setWindowTitle("Git durumu")
        box.setText(message)
        box.setDetailedText(details)
        box.setIcon(QMessageBox.Icon.Information if status.succeeded else QMessageBox.Icon.Warning)
        box.exec()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.usage, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config = dialog.result_config
            self._sync_header()
            self.statusBar().showMessage("Ayarlar kaydedildi.", 4000)

    def _start_github_sync(self, direction: str) -> None:
        if self.workspace.root is None:
            QMessageBox.warning(self, "Çalışma alanı gerekli", "Önce yerel depo klasörünü açın.")
            return
        if self.sync_worker and self.sync_worker.isRunning():
            return
        if (self.agent_worker and self.agent_worker.isRunning()) or (self.manual_worker and self.manual_worker.isRunning()):
            QMessageBox.information(self, "Çalışma alanı meşgul", "Önce ajan veya terminal işleminin bitmesini bekleyin.")
            return
        if not self.config.github_repository:
            QMessageBox.information(self, "GitHub yapılandırması", "Ayarlar bölümünde GitHub HTTPS depo adresini girin.")
            self._open_settings()
            return
        try:
            token = SecretStore.get_github_token()
        except RuntimeError as exc:
            QMessageBox.warning(self, "GitHub anahtarlığı kullanılamıyor", str(exc))
            return
        if not token:
            QMessageBox.information(self, "GitHub tokenı gerekli", "Ayarlar bölümünde tokenı işletim sistemi anahtarlığına kaydedin.")
            self._open_settings()
            return
        action = "Uzak depodan fast-forward pull" if direction == "pull" else "Yerel commit'leri GitHub'a push"
        warning = ("Pull yalnızca temiz çalışma ağacında fast-forward yapar." if direction == "pull"
                   else "Push yerel commit'leri ayarlardaki GitHub deposuna gönderir.")
        if QMessageBox.question(self, f"{action} onayı", f"{action}?\n\n{self.config.github_repository}\n{warning}",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        worker = GitSyncWorker(self.git, self.usage, self.session_id, direction,
                               self.config.github_repository, token, self.config.github_branch, self)
        self.sync_worker = worker
        worker.completed.connect(self._on_github_sync_complete)
        worker.finished.connect(self._github_sync_finished)
        self.github_button.setEnabled(False)
        self.statusBar().showMessage(f"GitHub {direction} çalışıyor…")
        worker.start()
        self._refresh_busy_controls()

    def _on_github_sync_complete(self, result: GitResult) -> None:
        if result.succeeded:
            QMessageBox.information(self, "GitHub eşitleme", result.stdout.strip() or "Git işlemi tamamlandı.")
        else:
            QMessageBox.warning(self, "GitHub eşitleme başarısız", result.stderr.strip() or result.stdout.strip() or f"Çıkış kodu: {result.exit_code}")
        self._update_git_status()

    def _github_sync_finished(self) -> None:
        worker = self.sync_worker
        self.sync_worker = None
        if worker:
            worker.deleteLater()
        self.github_button.setEnabled(True)
        self.statusBar().showMessage("GitHub işlemi tamamlandı.", 5000)
        self._update_usage()
        if self._close_when_workers_finish:
            self._try_deferred_close()

    def _open_usage_editor(self) -> None:
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            editor = Path(sys.executable).resolve().with_name("UsageLimitEditor.exe")
            command = [str(editor)]
        else:
            repository_root = Path(__file__).resolve().parents[2]
            script = repository_root / "usage_limit_editor.py"
            python = Path(sys.executable)
            if os.name == "nt" and python.name.lower() == "python.exe":
                candidate = python.with_name("pythonw.exe")
                if candidate.exists():
                    python = candidate
            command = [str(python), str(script)]
        if not frozen and not Path(command[1]).exists():
            QMessageBox.warning(self, "Kota aracı bulunamadı", "UsageLimitEditor uygulaması repo kökünde bulunamadı.")
            return
        try:
            subprocess.Popen(command, cwd=str(Path(command[-1]).parent), shell=False,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            QMessageBox.warning(self, "Kota aracı başlatılamadı", str(exc))

    def _open_safe_link(self, url: QUrl) -> None:
        if url.scheme() in {"https", "http"}:
            QDesktopServices.openUrl(url)

    def closeEvent(self, event) -> None:
        running = [worker for worker in (self.agent_worker, self.manual_worker, self.sync_worker) if worker and worker.isRunning()]
        if not running:
            if self._resolve_editor_changes():
                event.accept()
            else:
                event.ignore()
            return
        answer = QMessageBox.question(self, "Çalışan görevler var", "Kapanışta çalışan ajan/komut durdurulsun mu?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self._close_when_workers_finish = True
        if self.agent_worker and self.agent_worker.isRunning():
            self.agent_worker.stop()
        if self.manual_worker and self.manual_worker.isRunning():
            self.manual_worker.stop()
        event.ignore()
        self.statusBar().showMessage("Görevler durduruluyor; tamamlandığında uygulama kapanacak…")

    def _try_deferred_close(self) -> None:
        if all(not worker or not worker.isRunning() for worker in (self.agent_worker, self.manual_worker, self.sync_worker)):
            self._close_when_workers_finish = False
            QTimer.singleShot(0, self.close)
