# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# gui/pages/history_page.py
# Página de histórico de copytrades (leitura do SQLite).

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox
)
from PySide6.QtCore import Slot, Qt
from PySide6.QtGui import QColor
from datetime import datetime
from gui import themes

logger = logging.getLogger(__name__)


class HistoryPage(QWidget):
    def __init__(self, copytrade_manager=None, parent=None):
        super().__init__(parent)
        self.copytrade_manager = copytrade_manager
        self.setStyleSheet(themes.history_page_style())
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Historico de CopyTrades")
        title.setProperty("class", "page-title")
        header.addWidget(title)
        header.addStretch()

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["Todos", "Sucesso", "Falha"])
        self.filter_combo.currentIndexChanged.connect(lambda: self.refresh())
        header.addWidget(QLabel("Filtro:"))
        header.addWidget(self.filter_combo)

        refresh_btn = QPushButton("Atualizar")
        refresh_btn.setProperty("class", "action-btn")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)

        layout.addLayout(header)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(11)
        self.table.setHorizontalHeaderLabels([
            "Data/Hora", "Master", "Ticket M", "Simbolo", "Acao",
            "Lote M", "Slave", "Ticket S", "Lote S", "Status", "Motivo"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table, 1)

    @Slot()
    def refresh(self, _data=None):
        if not self.copytrade_manager:
            return

        try:
            rows = self.copytrade_manager.get_trade_history(limit=200)
        except Exception as e:
            logger.error(f"Erro ao carregar historico: {e}")
            return

        filter_text = self.filter_combo.currentText()

        filtered = []
        for row in rows:
            status = row.get("status", "")
            if filter_text == "Sucesso" and status != "SUCCESS":
                continue
            if filter_text == "Falha" and status != "FAILED":
                continue
            filtered.append(row)

        c = themes.t()
        self.table.setRowCount(len(filtered))
        for i, row in enumerate(filtered):
            ts_val = row.get("timestamp", 0)
            ts = datetime.fromtimestamp(ts_val).strftime("%Y-%m-%d %H:%M:%S") if ts_val else ""
            close_reason = row.get("close_reason", "") or ""
            motivo_labels = {
                "COPYTRADE": "CopyTrade",
                "BROKER_SLTP": "Broker SL/TP/SO",
                "EMERGENCY": "Emergência",
            }
            motivo = motivo_labels.get(close_reason, close_reason)
            values = [
                ts,                                  # Data/Hora
                str(row.get("master_broker", "")),   # Master
                str(row.get("master_ticket", "")),   # Ticket M
                str(row.get("symbol", "")),          # Simbolo
                str(row.get("action", "")),          # Acao
                str(row.get("master_lot", "")),      # Lote M
                str(row.get("slave_broker", "")),    # Slave
                str(row.get("slave_ticket", "")),    # Ticket S
                str(row.get("slave_lot", "")),       # Lote S
                str(row.get("status", "")),          # Status
                motivo,                              # Motivo
            ]
            for j, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                # Color code status
                if j == 9:
                    if val == "SUCCESS":
                        item.setForeground(QColor(c['success']))
                    elif val == "FAILED":
                        item.setForeground(QColor(c['error']))
                self.table.setItem(i, j, item)
