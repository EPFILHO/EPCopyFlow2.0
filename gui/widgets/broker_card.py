# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 002
# gui/widgets/broker_card.py
# Card visual para exibir informações de uma corretora.

import logging
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy
)
from PySide6.QtCore import Qt
from gui import themes

logger = logging.getLogger(__name__)

# Cores dos indicadores de status
_COLOR_GREEN = "#a6e3a1"
_COLOR_RED = "#f38ba8"
_COLOR_GRAY = "#585b70"


class BrokerCard(QFrame):
    def __init__(self, broker_key, broker_data, is_connected=False,
                 show_connect_btn=False, on_connect=None, on_disconnect=None,
                 parent=None):
        super().__init__(parent)
        self.broker_key = broker_key
        self.broker_data = broker_data
        self.is_connected = is_connected
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

        role = broker_data.get("role", "slave")
        is_master = role == "master"

        border_color, role_color, role_bg, status_color = themes.broker_card_dynamic_colors(
            is_master, is_connected
        )

        style = themes.broker_card_style(border_color, role_color, role_bg, status_color)
        self.setStyleSheet(style)
        self.setProperty("class", "broker-card")
        self.setMinimumWidth(280)
        self.setMaximumWidth(400)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self._init_ui(broker_key, broker_data, role, is_connected, show_connect_btn)

    def _init_ui(self, key, data, role, is_connected, show_connect_btn):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        # Row 1: Name + Role badge
        top_row = QHBoxLayout()
        broker_name = data.get("broker_name", key.split("-")[0])
        title = QLabel(f"{broker_name}")
        title.setProperty("class", "card-title")
        top_row.addWidget(title)
        top_row.addStretch()

        role_label = QLabel(role.upper())
        role_label.setProperty("class", "card-role")
        top_row.addWidget(role_label)
        layout.addLayout(top_row)

        # Row 2: Key + status
        row2 = QHBoxLayout()
        key_label = QLabel(key)
        key_label.setProperty("class", "card-info")
        row2.addWidget(key_label)
        row2.addStretch()

        self.status_label = QLabel("Conectado" if is_connected else "Desconectado")
        self.status_label.setProperty("class", "card-status")
        row2.addWidget(self.status_label)
        layout.addLayout(row2)

        # Row 3: Status indicators (MT5, BRK, ZMQ, EA, ALG)
        indicators_row = QHBoxLayout()
        indicators_row.setSpacing(12)
        self._indicators = {}
        for name in ("MT5", "BRK", "ZMQ", "EA", "ALG"):
            dot = QLabel("\u25CF")  # ● character
            dot.setStyleSheet(f"color: {_COLOR_GRAY}; font-size: 14px;")
            dot.setAlignment(Qt.AlignCenter)
            lbl = QLabel(name)
            lbl.setProperty("class", "card-info")
            lbl.setStyleSheet("font-size: 10px;")
            pair = QHBoxLayout()
            pair.setSpacing(2)
            pair.addWidget(dot)
            pair.addWidget(lbl)
            indicators_row.addLayout(pair)
            self._indicators[name] = dot
        indicators_row.addStretch()
        layout.addLayout(indicators_row)

        # Row 4: Client + Lot multiplier
        row4 = QHBoxLayout()
        client = data.get("client", data.get("name", "-"))
        client_label = QLabel(f"Cliente: {client}")
        client_label.setProperty("class", "card-info")
        row4.addWidget(client_label)
        row4.addStretch()

        mult = data.get("lot_multiplier", 1.0)
        mult_label = QLabel(f"Mult: {mult:.2f}x")
        mult_label.setProperty("class", "card-info")
        row4.addWidget(mult_label)
        layout.addLayout(row4)

        # Row 5: Balance + Positions count
        row5 = QHBoxLayout()
        self.balance_label = QLabel("Saldo: --")
        self.balance_label.setProperty("class", "card-info")
        row5.addWidget(self.balance_label)
        row5.addStretch()

        self.positions_label = QLabel("Posicoes: --")
        self.positions_label.setProperty("class", "card-info")
        row5.addWidget(self.positions_label)
        layout.addLayout(row5)

        # Row 6: Total profit
        self.profit_label = QLabel("P/L: --")
        self.profit_label.setProperty("class", "card-profit-positive")
        layout.addWidget(self.profit_label)

        # Connect/Disconnect button (only on brokers page)
        if show_connect_btn:
            btn_row = QHBoxLayout()
            btn_row.addStretch()
            if is_connected:
                btn = QPushButton("Desconectar")
                btn.setProperty("class", "card-disconnect")
                if self._on_disconnect:
                    btn.clicked.connect(lambda _checked=False: self._on_disconnect())
            else:
                btn = QPushButton("Conectar")
                btn.setProperty("class", "card-connect")
                if self._on_connect:
                    btn.clicked.connect(lambda _checked=False: self._on_connect())
            btn_row.addWidget(btn)
            layout.addLayout(btn_row)

    def update_status_indicators(self, mt5=None, brk=None, zmq=None, ea=None, alg=None):
        """Update the 5 status indicator dots.

        Each parameter accepts: True (green), False (red), None (gray).
        """
        mapping = {"MT5": mt5, "BRK": brk, "ZMQ": zmq, "EA": ea, "ALG": alg}
        for name, value in mapping.items():
            if name in self._indicators:
                if value is True:
                    color = _COLOR_GREEN
                elif value is False:
                    color = _COLOR_RED
                else:
                    color = _COLOR_GRAY
                self._indicators[name].setStyleSheet(f"color: {color}; font-size: 14px;")

    def update_positions(self, positions):
        count = len(positions) if positions else 0
        self.positions_label.setText(f"Posicoes: {count}")
        total_profit = sum(p.get("profit", 0) for p in positions) if positions else 0
        self._set_profit(total_profit)

    def update_balance(self, data):
        balance = data.get("balance", 0)
        equity = data.get("equity", 0)
        self.balance_label.setText(f"Saldo: {balance:,.2f}")

    def _set_profit(self, value):
        prefix = "+" if value >= 0 else ""
        self.profit_label.setText(f"P/L: {prefix}{value:,.2f}")
        prop = "card-profit-positive" if value >= 0 else "card-profit-negative"
        self.profit_label.setProperty("class", prop)
        self.profit_label.style().unpolish(self.profit_label)
        self.profit_label.style().polish(self.profit_label)
