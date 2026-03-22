# Arquivo: gui/widgets/boleta_open_orders_tab.py
# Versão: 1.0.9.m - Envio 3 (Modularização da aba de ordens abertas)

import logging
import time
import asyncio
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QDoubleSpinBox, QAbstractItemView, QDialog
)
from PySide6.QtCore import Slot, Qt

logger = logging.getLogger(__name__)

class BoletaOpenOrdersTab(QWidget):
    def __init__(self, broker_key, zmq_message_handler, broker_status, broker_modes, pending_tickets, close_order_callback, modify_order_callback, partial_close_callback, parent=None):
        super().__init__(parent)
        self.broker_key = broker_key
        self.zmq_message_handler = zmq_message_handler
        self.broker_status = broker_status
        self.broker_modes = broker_modes
        self.pending_tickets = pending_tickets
        self.close_order_callback = close_order_callback
        self.modify_order_callback = modify_order_callback
        self.partial_close_callback = partial_close_callback # <<-- Adicionado aqui na assinatura do __init__

        self.setup_ui()
        self._connect_signals()
        logger.debug(f"BoletaOpenOrdersTab inicializada para {self.broker_key}.")

    def setup_ui(self):
        layout = QVBoxLayout(self)
        table = QTableWidget()
        table.setColumnCount(11)
        table.setHorizontalHeaderLabels(
            ["Ticket", "Símbolo", "Tipo", "Volume", "Preço Entrada", "SL", "TP", "Lucro/Prejuízo", "Fechar",
             "Modificar", "Parcial"])

        table.setColumnWidth(0, 80)
        table.setColumnWidth(1, 100)
        table.setColumnWidth(2, 80)
        table.setColumnWidth(3, 80)
        table.setColumnWidth(4, 100)
        table.setColumnWidth(5, 80)
        table.setColumnWidth(6, 80)
        table.setColumnWidth(7, 120)
        table.setColumnWidth(8, 70)
        table.setColumnWidth(9, 70)
        table.setColumnWidth(10, 70)

        table.setMinimumHeight(400)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setAlternatingRowColors(True)
        table.setStyleSheet("""
            QTableWidget {
                alternate-background-color: #f0f0f0;
            }
        """)
        table.setObjectName(f"open_orders_{self.broker_key}")
        layout.addWidget(table)
        self.setLayout(layout)
        self.table = table # Armazena a referência da tabela

    def _connect_signals(self):
        self.zmq_message_handler.positions_received.connect(self._update_positions_gui)

    @Slot(dict)
    def _update_positions_gui(self, positions_data):
        if positions_data.get("broker_key") != self.broker_key:
            return

        positions = positions_data.get("data", positions_data.get("", []))
        open_positions = [pos for pos in positions if "PENDING" not in pos.get("type", "").upper()]

        self.table.clearContents()
        self.table.setRowCount(len(open_positions))
        for row, pos in enumerate(open_positions):
            self._populate_position_row(row, pos)
        logger.debug(f"Tabela de ordens abertas atualizada para {self.broker_key} com {len(open_positions)} linhas.")

    def _populate_position_row(self, row, pos):
        ticket_item = QTableWidgetItem(str(pos.get("ticket", "")))
        ticket_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 0, ticket_item)

        symbol_item = QTableWidgetItem(str(pos.get("symbol", "")))
        symbol_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 1, symbol_item)

        type_item = QTableWidgetItem(str(pos.get("type", "")))
        type_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 2, type_item)

        volume_item = QTableWidgetItem(f"{float(pos.get('volume', 0.0)):.2f}")
        volume_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 3, volume_item)

        price_open_item = QTableWidgetItem(f"{float(pos.get('price_open', 0.0)):.2f}")
        price_open_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 4, price_open_item)

        sl_item = QTableWidgetItem(f"{float(pos.get('sl', 0.0)):.2f}")
        sl_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 5, sl_item)

        tp_item = QTableWidgetItem(f"{float(pos.get('tp', 0.0)):.2f}")
        tp_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 6, tp_item)

        profit_item = QTableWidgetItem(f"{float(pos.get('profit', 0.0)):.2f}")
        profit_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 7, profit_item)

        close_btn = QPushButton("✕")
        close_btn.setMinimumHeight(30)
        close_btn.setStyleSheet("color: red; padding: 0px; margin: 0px;")
        close_btn.clicked.connect(lambda checked: self.close_order_callback(row, self.broker_key, self.table))

        modify_btn = QPushButton("⚠")
        modify_btn.setMinimumHeight(30)
        modify_btn.setStyleSheet("padding: 0px; margin: 0px;")
        modify_btn.clicked.connect(lambda checked: self.modify_order_callback(row, self.broker_key, self.table))

        partial_btn = QPushButton("½")
        partial_btn.setMinimumHeight(30)
        partial_btn.setStyleSheet("padding: 0px; margin: 0px;")
        # AQUI O partial_close_callback É USADO!
        partial_btn.clicked.connect(lambda checked: self.partial_close_callback(row, self.broker_key, self.table))

        enabled = self.broker_key in self.broker_status and self.broker_status[self.broker_key]
        close_btn.setEnabled(enabled)
        modify_btn.setEnabled(enabled)
        partial_btn.setEnabled(enabled)

        self.table.setCellWidget(row, 8, close_btn)
        self.table.setCellWidget(row, 9, modify_btn)
        self.table.setCellWidget(row, 10, partial_btn)
        logger.debug(f"Botões de ação adicionados para ordem na linha {row} de {self.broker_key}.")

# Arquivo: gui/widgets/boleta_open_orders_tab.py
# Versão: 1.0.9.m - Envio 3 (Modularização da aba de ordens abertas)