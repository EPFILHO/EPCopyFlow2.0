# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# gui/pages/brokers_page.py
# Página de gerenciamento de corretoras: cadastro, conexão/desconexão.

import logging
import asyncio
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QGridLayout, QMessageBox
)
from PySide6.QtCore import Slot, Qt, Signal
from gui.brokers_dialog import BrokersDialog
from gui.widgets.broker_card import BrokerCard
from gui import themes

logger = logging.getLogger(__name__)


class BrokersPage(QWidget):
    broker_status_changed = Signal()

    def __init__(self, config, broker_manager, zmq_router, mt5_monitor,
                 zmq_message_handler=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.broker_manager = broker_manager
        self.zmq_router = zmq_router
        self.mt5_monitor = mt5_monitor
        self.zmq_message_handler = zmq_message_handler
        self._broker_status = {}
        self.broker_cards = {}
        self.setStyleSheet(themes.brokers_page_style())
        self._init_ui()
        self.refresh_brokers()

    def set_broker_status(self, broker_status):
        """Reference to main_window.broker_status dict."""
        self._broker_status = broker_status

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

        # Master section
        master_label = QLabel("Master")
        master_label.setProperty("class", "section-title")
        layout.addWidget(master_label)

        self.master_area = QHBoxLayout()
        self.master_placeholder = QLabel("Nenhum Master configurado")
        self.master_placeholder.setStyleSheet(themes.dashboard_placeholder_style())
        self.master_area.addWidget(self.master_placeholder)
        self.master_area.addStretch()
        layout.addLayout(self.master_area)

        # Slaves section
        slaves_label = QLabel("Slaves")
        slaves_label.setProperty("class", "section-title")
        layout.addWidget(slaves_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(themes.scroll_area_style())
        scroll_widget = QWidget()
        scroll_widget.setStyleSheet(themes.scroll_widget_style())
        self.slaves_grid = QGridLayout(scroll_widget)
        self.slaves_grid.setContentsMargins(0, 0, 0, 0)
        self.slaves_grid.setSpacing(12)
        self.slaves_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, 1)

    def apply_theme(self):
        self.setStyleSheet(themes.brokers_page_style())
        self.refresh_brokers()

    def refresh_brokers(self):
        for card in self.broker_cards.values():
            card.setParent(None)
            card.deleteLater()
        self.broker_cards.clear()

        # Remove master placeholder if present
        if self.master_placeholder.parent():
            self.master_placeholder.setParent(None)

        # Clear slaves grid
        while self.slaves_grid.count():
            item = self.slaves_grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        brokers = self.broker_manager.get_brokers()
        connected = self.broker_manager.get_connected_brokers()
        master_key = self.broker_manager.get_master_broker()
        slave_keys = sorted([k for k in brokers if k != master_key])

        # Master card
        if master_key:
            card = BrokerCard(
                master_key, brokers[master_key],
                is_connected=(master_key in connected),
                show_connect_btn=True,
                on_connect=lambda k=master_key: self._connect_broker(k),
                on_disconnect=lambda k=master_key: self._disconnect_broker(k),
            )
            self.broker_cards[master_key] = card
            self.master_area.insertWidget(0, card)
        else:
            self.master_area.insertWidget(0, self.master_placeholder)

        # Slave cards in grid
        cols = 3
        for i, key in enumerate(slave_keys):
            card = BrokerCard(
                key, brokers[key],
                is_connected=(key in connected),
                show_connect_btn=True,
                on_connect=lambda k=key: self._connect_broker(k),
                on_disconnect=lambda k=key: self._disconnect_broker(k),
            )
            self.broker_cards[key] = card
            self.slaves_grid.addWidget(card, i // cols, i % cols)

        self.update_broker_indicators()

    @Slot()
    def update_broker_indicators(self):
        """Update all 5 status indicators on every broker card."""
        trade_allowed = {}
        connection_status = {}
        if self.zmq_message_handler:
            trade_allowed = self.zmq_message_handler.get_trade_allowed_states()
            connection_status = self.zmq_message_handler.get_connection_status_states()

        for key, card in self.broker_cards.items():
            process = self.broker_manager.mt5_processes.get(key)
            mt5_running = process is not None and process.poll() is None

            if not mt5_running:
                card.update_status_indicators(mt5=None, brk=None, zmq=None, ea=None, alg=None)
                continue

            is_connected = key in self.broker_manager.get_connected_brokers()
            if not is_connected:
                card.update_status_indicators(mt5=True, brk=None, zmq=None, ea=None, alg=None)
                continue

            ea_registered = self._broker_status.get(key, False)
            alg = trade_allowed.get(key)
            brk = connection_status.get(key)

            card.update_status_indicators(
                mt5=True,
                brk=brk,
                zmq=True,
                ea=ea_registered if ea_registered else False,
                alg=alg,
            )

    def _open_broker_dialog(self):
        dialog = BrokersDialog(self.config, self.broker_manager, parent=self)
        dialog.brokers_updated.connect(self.refresh_brokers)
        dialog.exec()

    def _connect_broker(self, key):
        try:
            self.broker_manager.connect_broker(key)
            logger.info(f"Corretora {key} conectada.")
            self.refresh_brokers()
            self.broker_status_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao conectar {key}: {e}")
            logger.error(f"Erro ao conectar {key}: {e}")

    def _disconnect_broker(self, key):
        try:
            self.broker_manager.disconnect_broker(key)
            logger.info(f"Corretora {key} desconectada.")
            self.refresh_brokers()
            self.broker_status_changed.emit()
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
        self.broker_status_changed.emit()

    def _disconnect_all(self):
        connected = list(self.broker_manager.get_connected_brokers())
        for key in connected:
            try:
                self.broker_manager.disconnect_broker(key)
            except Exception as e:
                logger.error(f"Erro ao desconectar {key}: {e}")
        self.refresh_brokers()
        self.broker_status_changed.emit()
