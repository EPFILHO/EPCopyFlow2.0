# gui/widgets/notification_center.py
# Centro de notificações global exibido no centro da barra superior.
# Substitui QMessageBox modais por um widget não-modal: fica oculto quando
# não há notificações, aparece com ícone+contador colorido pela maior
# severidade, pisca em WARNING/ERROR, e abre popup com histórico ao clicar.
# Auto-dispensa INFO após 5s; WARNING/ERROR só saem por clique.

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Deque, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from gui import themes

logger = logging.getLogger(__name__)


class NotificationLevel(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2


@dataclass
class Notification:
    level: NotificationLevel
    title: str
    detail: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


_LEVEL_ICON = {
    NotificationLevel.INFO: "i",
    NotificationLevel.WARNING: "!",
    NotificationLevel.ERROR: "X",
}


class NotificationCenter(QPushButton):
    """Badge da barra superior para notificações globais.

    API principal: ``push(level, title, detail)``. Seguro pra chamar
    do thread GUI (direto de slots Qt).
    """

    MAX_HISTORY = 50
    INFO_AUTO_DISMISS_MS = 5000
    BLINK_INTERVAL_MS = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: Deque[Notification] = deque(maxlen=self.MAX_HISTORY)
        self._unread: int = 0
        self._max_level: Optional[NotificationLevel] = None
        self._popup: Optional["NotificationPopup"] = None
        self._blink_on: bool = False

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(self.BLINK_INTERVAL_MS)
        self._blink_timer.timeout.connect(self._toggle_blink)

        self._info_dismiss_timer = QTimer(self)
        self._info_dismiss_timer.setSingleShot(True)
        self._info_dismiss_timer.timeout.connect(self._on_info_dismiss)

        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.clicked.connect(self._open_popup)

        self._refresh_ui()

    # ──────────────────────────────────────────────
    # API pública
    # ──────────────────────────────────────────────
    def push(self, level, title: str, detail: str = ""):
        """Adiciona uma notificação. Aceita ``NotificationLevel`` ou nome."""
        if isinstance(level, str):
            try:
                level = NotificationLevel[level.upper()]
            except KeyError:
                level = NotificationLevel.INFO
        if not isinstance(level, NotificationLevel):
            level = NotificationLevel(int(level))

        notif = Notification(level=level, title=title, detail=detail)
        self._history.append(notif)
        self._unread += 1
        if self._max_level is None or level > self._max_level:
            self._max_level = level

        popup_open = self._popup is not None and self._popup.isVisible()

        if popup_open:
            # Usuário já está olhando — só atualiza a lista, sem beep/blink.
            self._popup.add_item(notif)
            self._acknowledge()
        else:
            # Beep do sistema para WARNING/ERROR
            if level >= NotificationLevel.WARNING:
                try:
                    QApplication.beep()
                except Exception:
                    pass
                # Piscar
                if not self._blink_timer.isActive():
                    self._blink_on = True
                    self._blink_timer.start()
                # Nível subiu — cancela auto-dismiss de INFO se estava rodando
                self._info_dismiss_timer.stop()
            else:
                # INFO: agenda auto-dismiss (reinicia se novo INFO chegar)
                self._info_dismiss_timer.start(self.INFO_AUTO_DISMISS_MS)

        self._refresh_ui()

    def apply_theme(self):
        """Reaplica stylesheet após troca de tema."""
        self._refresh_ui()
        if self._popup is not None:
            self._popup.apply_theme()

    def shutdown(self):
        """Para timers e fecha popup — chamar no closeEvent da janela."""
        self._blink_timer.stop()
        self._info_dismiss_timer.stop()
        if self._popup is not None:
            try:
                self._popup.close()
            except Exception:
                pass
            self._popup = None

    # ──────────────────────────────────────────────
    # Internos
    # ──────────────────────────────────────────────
    def _on_info_dismiss(self):
        # Só dispensa se ainda é INFO (não foi promovido por WARNING/ERROR).
        if self._max_level == NotificationLevel.INFO:
            self._acknowledge()

    def _toggle_blink(self):
        self._blink_on = not self._blink_on
        self._refresh_ui()

    def _acknowledge(self):
        self._unread = 0
        self._max_level = None
        self._blink_timer.stop()
        self._blink_on = False
        self._info_dismiss_timer.stop()
        self._refresh_ui()

    def _open_popup(self):
        if self._popup is None:
            self._popup = NotificationPopup(list(self._history), self.window())
            self._popup.closed.connect(self._on_popup_closed)
            self._popup.cleared.connect(self._on_popup_cleared)
        else:
            self._popup.set_items(list(self._history))

        # Acknowledge ao abrir (para de piscar, contador zera).
        # Feito ANTES de mostrar para o refresh já ocultar o badge.
        self._acknowledge()

        # Posicionar logo abaixo do botão, centralizado nele.
        btn_global = self.mapToGlobal(self.rect().bottomLeft())
        popup_w = self._popup.width()
        target_x = btn_global.x() + (self.width() // 2) - (popup_w // 2)
        target_y = btn_global.y() + 4

        # Mantém dentro da tela se possível
        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            if target_x + popup_w > geo.right():
                target_x = geo.right() - popup_w - 8
            if target_x < geo.left():
                target_x = geo.left() + 8

        self._popup.move(target_x, target_y)
        self._popup.show()
        self._popup.raise_()
        self._popup.activateWindow()

    def _on_popup_closed(self):
        self._popup = None

    def _on_popup_cleared(self):
        self._history.clear()
        self._acknowledge()

    def _refresh_ui(self):
        if self._unread == 0:
            self.setVisible(False)
            self.setText("")
            return

        self.setVisible(True)
        level = self._max_level or NotificationLevel.INFO
        icon = _LEVEL_ICON.get(level, "·")

        last = self._history[-1] if self._history else None
        if last:
            label = last.title
            if self._unread > 1:
                text = f"  [{icon}]  {label}  (+{self._unread - 1})"
            else:
                text = f"  [{icon}]  {label}"
        else:
            text = f"  [{icon}]  "

        self.setText(text)
        self.setToolTip(
            f"{self._unread} notificacao(oes) nao lida(s) — clique para ver"
        )
        self.setStyleSheet(self._current_stylesheet(level))

    def _current_stylesheet(self, level: NotificationLevel) -> str:
        c = themes.t()
        color_map = {
            NotificationLevel.INFO: c["accent"],
            NotificationLevel.WARNING: c["warning"],
            NotificationLevel.ERROR: c["error"],
        }
        fg = color_map[level]
        btn_text = c["btn_text_on_color"]
        base_bg = c["base"]

        # Durante o pisca em WARNING/ERROR, alterna fundo cheio x vazio.
        blinking = self._blink_on and level >= NotificationLevel.WARNING
        if blinking:
            bg = fg
            text_color = btn_text
        else:
            bg = base_bg
            text_color = fg

        return (
            "QPushButton {"
            f"  background-color: {bg};"
            f"  color: {text_color};"
            f"  border: 1px solid {fg};"
            "  border-radius: 6px;"
            "  padding: 4px 14px;"
            "  font-size: 12px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover {"
            f"  background-color: {fg};"
            f"  color: {btn_text};"
            "}"
        )


class NotificationPopup(QDialog):
    """Popup não-modal com a lista de notificações recentes."""

    closed = Signal()
    cleared = Signal()

    def __init__(self, items: List[Notification], parent=None):
        # Sem Qt.Popup — queremos que o usuário possa interagir
        # livremente com a janela principal enquanto a lista está aberta.
        super().__init__(parent)
        self.setWindowTitle("Notificacoes")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setModal(False)
        self.resize(440, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self._title_label = QLabel("Notificacoes recentes")
        self._title_label.setObjectName("notif-popup-title")
        header.addWidget(self._title_label)
        header.addStretch()
        self._clear_btn = QPushButton("Limpar")
        self._clear_btn.setObjectName("notif-popup-btn")
        self._clear_btn.clicked.connect(self._on_clear)
        header.addWidget(self._clear_btn)
        self._close_btn = QPushButton("Fechar")
        self._close_btn.setObjectName("notif-popup-btn")
        self._close_btn.clicked.connect(self.close)
        header.addWidget(self._close_btn)
        layout.addLayout(header)

        self._list = QListWidget()
        self._list.setObjectName("notif-popup-list")
        layout.addWidget(self._list)

        self.set_items(items)
        self.apply_theme()

    def set_items(self, items: List[Notification]):
        self._list.clear()
        if not items:
            placeholder = QListWidgetItem("  (sem notificacoes)")
            placeholder.setFlags(Qt.NoItemFlags)
            self._list.addItem(placeholder)
            return
        # mais recentes primeiro
        for notif in reversed(items):
            self._append_item(notif, at_top=False)

    def add_item(self, notif: Notification):
        # Remove placeholder se existir
        if self._list.count() == 1:
            first = self._list.item(0)
            if first is not None and first.flags() == Qt.NoItemFlags:
                self._list.takeItem(0)
        self._append_item(notif, at_top=True)

    def _append_item(self, notif: Notification, at_top: bool):
        text = self._format_line(notif)
        item = QListWidgetItem(text)
        c = themes.t()
        color_map = {
            NotificationLevel.INFO: c["accent"],
            NotificationLevel.WARNING: c["warning"],
            NotificationLevel.ERROR: c["error"],
        }
        from PySide6.QtGui import QColor
        item.setForeground(QColor(color_map[notif.level]))
        if at_top:
            self._list.insertItem(0, item)
        else:
            self._list.addItem(item)

    @staticmethod
    def _format_line(notif: Notification) -> str:
        ts = notif.timestamp.strftime("%H:%M:%S")
        icon = _LEVEL_ICON.get(notif.level, "·")
        if notif.detail:
            return f"{ts}  [{icon}]  {notif.title}  —  {notif.detail}"
        return f"{ts}  [{icon}]  {notif.title}"

    def _on_clear(self):
        self.cleared.emit()
        self.set_items([])

    def apply_theme(self):
        c = themes.t()
        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {c['card']};
                border: 1px solid {c['border_hover']};
                border-radius: 8px;
            }}
            QLabel#notif-popup-title {{
                color: {c['text']};
                font-size: 13px;
                font-weight: bold;
            }}
            QListWidget#notif-popup-list {{
                background-color: {c['base']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-radius: 6px;
                font-size: 12px;
                padding: 4px;
            }}
            QListWidget#notif-popup-list::item {{
                padding: 6px 4px;
                border-bottom: 1px solid {c['border']};
            }}
            QPushButton#notif-popup-btn {{
                background-color: {c['input']};
                color: {c['text']};
                border: 1px solid {c['border_hover']};
                border-radius: 4px;
                padding: 5px 14px;
                font-size: 12px;
            }}
            QPushButton#notif-popup-btn:hover {{
                background-color: {c['border_hover']};
            }}
            """
        )

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
