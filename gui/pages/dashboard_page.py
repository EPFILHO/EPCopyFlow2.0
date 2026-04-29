# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 002
# gui/pages/dashboard_page.py
# Página principal com cards de corretoras e resumo do copytrade.

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QGridLayout, QSizePolicy
)
from PySide6.QtCore import Slot, Qt
from gui.widgets.broker_card import BrokerCard
from gui import themes

logger = logging.getLogger(__name__)


class DashboardPage(QWidget):
    def __init__(self, broker_manager, copytrade_manager=None,
                 tcp_message_handler=None, parent=None):
        super().__init__(parent)
        self.broker_manager = broker_manager
        self.copytrade_manager = copytrade_manager
        self.tcp_message_handler = tcp_message_handler
        self._broker_status = {}  # EA registered: {key: True/False}
        self.broker_cards = {}
        self.setStyleSheet(themes.dashboard_style())
        self._init_ui()
        if self.copytrade_manager is not None:
            self.copytrade_manager.today_stats_ready.connect(self._on_today_stats_ready)
        self.refresh_brokers()

    def set_broker_status(self, broker_status):
        """Reference to main_window.broker_status dict."""
        self._broker_status = broker_status

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)

        title = QLabel("Dashboard")
        title.setProperty("class", "page-title")
        layout.addWidget(title)

        # Stats row
        stats_layout = QHBoxLayout()
        self.stat_total = self._create_stat_card("Total Copias", "0")
        self.stat_success = self._create_stat_card("Sucesso", "0")
        self.stat_failed = self._create_stat_card("Falhas", "0")
        self.stat_brokers = self._create_stat_card("Corretoras", "0")
        stats_layout.addWidget(self.stat_total)
        stats_layout.addWidget(self.stat_success)
        stats_layout.addWidget(self.stat_failed)
        stats_layout.addWidget(self.stat_brokers)
        layout.addLayout(stats_layout)

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

    def _create_stat_card(self, label_text, value_text):
        card = QFrame()
        card.setProperty("class", "stat-card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)

        value = QLabel(value_text)
        value.setProperty("class", "stat-value")
        value.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(value)

        label = QLabel(label_text)
        label.setProperty("class", "stat-label")
        label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(label)

        card._value_label = value
        return card

    def apply_theme(self):
        self.setStyleSheet(themes.dashboard_style())
        self.master_placeholder.setStyleSheet(themes.dashboard_placeholder_style())
        self.refresh_brokers()

    def refresh_brokers(self):
        """Rebuild broker cards from broker_manager data."""
        # Clear existing cards
        for card in self.broker_cards.values():
            card.setParent(None)
            card.deleteLater()
        self.broker_cards.clear()

        # Remove master placeholder if present
        if self.master_placeholder.parent():
            self.master_placeholder.setParent(None)

        brokers = self.broker_manager.get_brokers()
        connected = self.broker_manager.get_connected_brokers()

        master_key = self.broker_manager.get_master_broker()
        slave_keys = sorted([k for k in brokers if k != master_key])

        # Master card
        if master_key:
            card = BrokerCard(master_key, brokers[master_key], is_connected=(master_key in connected))
            self.broker_cards[master_key] = card
            self.master_area.insertWidget(0, card)
        else:
            self.master_area.insertWidget(0, self.master_placeholder)

        # Slave cards in grid
        while self.slaves_grid.count():
            item = self.slaves_grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        cols = 3
        for i, key in enumerate(slave_keys):
            card = BrokerCard(key, brokers[key], is_connected=(key in connected))
            self.broker_cards[key] = card
            self.slaves_grid.addWidget(card, i // cols, i % cols)

        # Update stats
        self.stat_brokers._value_label.setText(str(len(brokers)))
        self._update_copytrade_stats()

        # Update indicators after rebuilding cards
        self.update_broker_indicators()

    def _update_copytrade_stats(self):
        """Pede stats ao motor. Resposta chega via _on_today_stats_ready."""
        if not self.copytrade_manager:
            return
        self.copytrade_manager.request_today_stats()

    @Slot(dict)
    def refresh_stats(self, _data=None):
        """Slot público para refresh dos stat cards. Conectado a
        copy_trade_executed/copy_trade_failed em main_window — atualiza
        os cards a cada trade replicado."""
        self._update_copytrade_stats()

    @Slot(dict)
    def _on_today_stats_ready(self, stats):
        """Slot do signal cross-thread. Roda na main thread."""
        self.stat_total._value_label.setText(str(stats.get("total", 0)))
        self.stat_success._value_label.setText(str(stats.get("success", 0)))
        self.stat_failed._value_label.setText(str(stats.get("failed", 0)))

    @Slot()
    def update_broker_indicators(self):
        """Update all 4 status indicators (MT5, EA, BRK, ALG) on every broker card."""
        trade_allowed = {}
        connection_status = {}
        if self.tcp_message_handler:
            trade_allowed = self.tcp_message_handler.get_trade_allowed_states()
            connection_status = self.tcp_message_handler.get_connection_status_states()

        for key, card in self.broker_cards.items():
            # MT5: processo rodando?
            process = self.broker_manager.mt5_processes.get(key)
            mt5_running = process is not None and process.poll() is None

            if not mt5_running:
                # MT5 fechado: tudo cinza
                card.update_status_indicators(mt5=None, ea=None, brk=None, alg=None)
                continue

            # EA registrado? (prova real de que TCP está funcionando)
            ea_registered = self._broker_status.get(key, False)

            if not ea_registered:
                # MT5 rodando mas EA não registrou: MT5 verde, EA vermelho, resto cinza
                card.update_status_indicators(mt5=True, ea=False, brk=None, alg=None)
                continue

            # EA registrado: verificar BRK e ALG via buffers
            brk = connection_status.get(key)
            alg = trade_allowed.get(key)

            card.update_status_indicators(
                mt5=True,
                ea=True,
                brk=brk,
                alg=alg,
            )

    @Slot(dict)
    def update_positions(self, data):
        """Update position info on broker cards."""
        broker_key = data.get("broker_key")
        if broker_key and broker_key in self.broker_cards:
            positions = data.get("positions", [])
            self.broker_cards[broker_key].update_positions(positions)

    @Slot(dict)
    def update_balance(self, data):
        """Update balance info on broker cards."""
        broker_key = data.get("broker_key")
        if broker_key and broker_key in self.broker_cards:
            self.broker_cards[broker_key].update_balance(data)
