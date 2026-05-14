# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# gui/pages/logs_page.py
# Página dedicada de logs do sistema.

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
)
from PySide6.QtCore import Slot, Qt
from gui import themes

logger = logging.getLogger(__name__)


class LogsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(themes.logs_page_style())
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Logs do Sistema")
        title.setProperty("class", "page-title")
        header.addWidget(title)
        header.addStretch()

        clear_btn = QPushButton("Limpar")
        clear_btn.setProperty("class", "action-btn")
        clear_btn.clicked.connect(self._clear_logs)
        header.addWidget(clear_btn)

        layout.addLayout(header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        # Limite de linhas: evita o QTextEdit virar monstro de memória depois
        # de horas de operação. Linhas mais antigas são descartadas.
        self.log_text.document().setMaximumBlockCount(1000)
        layout.addWidget(self.log_text, 1)

    @Slot(str)
    def append_log(self, message: str):
        self.log_text.append(message)
        # Auto-scroll to bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_logs(self):
        self.log_text.clear()
