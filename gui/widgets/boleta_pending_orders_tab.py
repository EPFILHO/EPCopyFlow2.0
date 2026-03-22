# Arquivo: gui/widgets/boleta_pending_orders_tab.py
# Versão: 1.0.9.m - Envio 2 (Ajustado para receber e exibir ordens pendentes)

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QPushButton,
    QAbstractItemView
)
from PySide6.QtCore import Slot, Qt

logger = logging.getLogger(__name__)

class BoletaPendingOrdersTab(QWidget):
    def __init__(self, broker_key, zmq_message_handler, broker_status, broker_modes, pending_tickets,
                 close_order_callback, modify_order_callback, parent=None):
        super().__init__(parent)
        self.broker_key = broker_key
        self.zmq_message_handler = zmq_message_handler
        self.broker_status = broker_status
        self.broker_modes = broker_modes
        self.pending_tickets = pending_tickets
        self.close_order_callback = close_order_callback
        self.modify_order_callback = modify_order_callback

        self.setup_ui()
        self._connect_signals()
        logger.debug(f"BoletaPendingOrdersTab inicializada para {self.broker_key}.")

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["Ticket", "Símbolo", "Tipo", "Volume", "Preço", "SL", "TP", "Fechar", "Modificar"])

        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 80)
        self.table.setColumnWidth(6, 80)
        self.table.setColumnWidth(7, 70)
        self.table.setColumnWidth(8, 70)

        self.table.setMinimumHeight(400)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                alternate-background-color: #f0f0f0;
            }
        """)
        self.table.setObjectName(f"pending_orders_{self.broker_key}")
        layout.addWidget(self.table)
        self.setLayout(layout)

    def _connect_signals(self):
        # Conecta ao sinal orders_received para obter ordens pendentes
        self.zmq_message_handler.orders_received.connect(self.update_data)

    @Slot(dict)
    def update_data(self, orders_data): # Renomeado de positions_data para orders_data
        if orders_data.get("broker_key") != self.broker_key:
            return

        # Acessa a lista de ordens do dicionário de resposta
        orders = orders_data.get("orders", orders_data.get("", []))

        # Filtra por ordens pendentes (LIMIT, STOP, etc.)
        # O EA retorna tipos como "ORDER_TYPE_BUY_LIMIT", "ORDER_TYPE_SELL_STOP"
        pending_orders = [
            ord for ord in orders
            if "LIMIT" in ord.get("type", "").upper() or "STOP" in ord.get("type", "").upper()
        ]

        self.table.clearContents()
        self.table.setRowCount(len(pending_orders))
        for row, ord in enumerate(pending_orders): # Iterar sobre 'ord' em vez de 'pos'
            self._populate_pending_row(row, ord)
        logger.debug(f"Tabela de posições pendentes atualizada para {self.broker_key} com {len(pending_orders)} linhas.")

    def _populate_pending_row(self, row, ord): # Renomeado de pos para ord
        ticket_item = QTableWidgetItem(str(ord.get("ticket", "")))
        ticket_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 0, ticket_item)

        symbol_item = QTableWidgetItem(str(ord.get("symbol", "")))
        symbol_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 1, symbol_item)

        type_item = QTableWidgetItem(str(ord.get("type", "")))
        type_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 2, type_item)

        volume_item = QTableWidgetItem(f"{float(ord.get('volume', 0.0)):.2f}")
        volume_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 3, volume_item)

        # Para ordens pendentes, o preço é o preço de ativação, não price_open
        price_item = QTableWidgetItem(f"{float(ord.get('price', 0.0)):.2f}")
        price_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 4, price_item)

        sl_item = QTableWidgetItem(f"{float(ord.get('sl', 0.0)):.2f}")
        sl_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 5, sl_item)

        tp_item = QTableWidgetItem(f"{float(ord.get('tp', 0.0)):.2f}")
        tp_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 6, tp_item)

        close_btn = QPushButton("✕")
        close_btn.setMinimumHeight(30)
        close_btn.setStyleSheet("color: red; padding: 0px; margin: 0px;")
        close_btn.clicked.connect(lambda: self.close_order_callback(row, self.broker_key, self.table))

        modify_btn = QPushButton("⚠")
        modify_btn.setMinimumHeight(30)
        modify_btn.setStyleSheet("padding: 0px; margin: 0px;")
        modify_btn.clicked.connect(lambda: self.modify_order_callback(row, self.broker_key, self.table))

        enabled = self.broker_key in self.broker_status and self.broker_status[self.broker_key]
        close_btn.setEnabled(enabled)
        modify_btn.setEnabled(enabled)

        self.table.setCellWidget(row, 7, close_btn)
        self.table.setCellWidget(row, 8, modify_btn)

# Arquivo: gui/widgets/boleta_pending_orders_tab.py
# Versão: 1.0.9.m - Envio 2 (Ajustado para receber e exibir ordens pendentes)
