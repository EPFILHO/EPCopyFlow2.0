# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# gui/pages/brokers_page.py
# Página de gerenciamento de corretoras: cadastro, conexão/desconexão.

import logging
import asyncio
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QGridLayout, QMessageBox
)
from PySide6.QtCore import Slot, Qt
from gui.brokers_dialog import BrokersDialog
from gui.widgets.broker_card import BrokerCard

logger = logging.getLogger(__name__)

PAGE_STYLE = """
QLabel.page-title {
    color: #cdd6f4;
    font-size: 20px;
    font-weight: bold;
    padding: 8px 0px;
}
QPushButton.action-btn {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
}
QPushButton.action-btn:hover {
    background-color: #45475a;
}
QPushButton.connect-btn {
    background-color: #a6e3a1;
    color: #1e1e2e;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}
QPushButton.connect-btn:hover {
    background-color: #94e2d5;
}
QPushButton.disconnect-btn {
    background-color: #f38ba8;
    color: #1e1e2e;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}
QPushButton.disconnect-btn:hover {
    background-color: #eba0ac;
}
"""


class BrokersPage(QWidget):
    def __init__(self, config, broker_manager, zmq_router, mt5_monitor, parent=None):
        super().__init__(parent)
        self.config = config
        self.broker_manager = broker_manager
        self.zmq_router = zmq_router
        self.mt5_monitor = mt5_monitor
        self.broker_cards = {}
        self.setStyleSheet(PAGE_STYLE)
        self._init_ui()
        self.refresh_brokers()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)

        # Title + action buttons
        header = QHBoxLayout()
        title = QLabel("Corretoras")
        title.setProperty("class", "page-title")
        header.addWidget(title)
        header.addStretch()

        self.cadastro_btn = QPushButton("Cadastrar / Editar")
        self.cadastro_btn.setProperty("class", "action-btn")
        self.cadastro_btn.clicked.connect(self._open_broker_dialog)
        header.addWidget(self.cadastro_btn)

        self.connect_all_btn = QPushButton("Conectar Todas")
        self.connect_all_btn.setProperty("class", "connect-btn")
        self.connect_all_btn.clicked.connect(self._connect_all)
        header.addWidget(self.connect_all_btn)

        self.disconnect_all_btn = QPushButton("Desconectar Todas")
        self.disconnect_all_btn.setProperty("class", "disconnect-btn")
        self.disconnect_all_btn.clicked.connect(self._disconnect_all)
        header.addWidget(self.disconnect_all_btn)

        layout.addLayout(header)

        # Broker cards grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll_widget = QWidget()
        scroll_widget.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(scroll_widget)
        self.grid.setSpacing(12)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, 1)

    def refresh_brokers(self):
        for card in self.broker_cards.values():
            card.setParent(None)
            card.deleteLater()
        self.broker_cards.clear()

        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        brokers = self.broker_manager.get_brokers()
        connected = self.broker_manager.get_connected_brokers()
        cols = 3

        for i, key in enumerate(sorted(brokers.keys())):
            is_conn = key in connected
            card = BrokerCard(
                key, brokers[key], is_connected=is_conn,
                show_connect_btn=True,
                on_connect=lambda k=key: self._connect_broker(k),
                on_disconnect=lambda k=key: self._disconnect_broker(k),
            )
            self.broker_cards[key] = card
            self.grid.addWidget(card, i // cols, i % cols)

    def _open_broker_dialog(self):
        dialog = BrokersDialog(self.config, self.broker_manager, parent=self)
        dialog.brokers_updated.connect(self.refresh_brokers)
        dialog.exec()

    def _connect_broker(self, key):
        try:
            self.broker_manager.connect_broker(key)
            logger.info(f"Corretora {key} conectada.")
            self.refresh_brokers()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao conectar {key}: {e}")
            logger.error(f"Erro ao conectar {key}: {e}")

    def _disconnect_broker(self, key):
        try:
            self.broker_manager.disconnect_broker(key)
            logger.info(f"Corretora {key} desconectada.")
            self.refresh_brokers()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao desconectar {key}: {e}")
            logger.error(f"Erro ao desconectar {key}: {e}")

    def _connect_all(self):
        brokers = self.broker_manager.get_brokers()
        connected = self.broker_manager.get_connected_brokers()
        for key in brokers:
            if key not in connected:
                try:
                    self.broker_manager.connect_broker(key)
                except Exception as e:
                    logger.error(f"Erro ao conectar {key}: {e}")
        self.refresh_brokers()

    def _disconnect_all(self):
        connected = list(self.broker_manager.get_connected_brokers())
        for key in connected:
            try:
                self.broker_manager.disconnect_broker(key)
            except Exception as e:
                logger.error(f"Erro ao desconectar {key}: {e}")
        self.refresh_brokers()
