# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# gui/pages/history_page.py
# Página de histórico de copytrades (leitura do SQLite).

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox
)
from PySide6.QtCore import Slot, Qt
from datetime import datetime

logger = logging.getLogger(__name__)

PAGE_STYLE = """
QLabel.page-title {
    color: #cdd6f4;
    font-size: 20px;
    font-weight: bold;
    padding: 8px 0px;
}
QTableWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    gridline-color: #313244;
    selection-background-color: #45475a;
}
QTableWidget::item {
    padding: 6px;
}
QHeaderView::section {
    background-color: #313244;
    color: #cdd6f4;
    padding: 6px;
    border: none;
    font-weight: bold;
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
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 8px;
}
"""


class HistoryPage(QWidget):
    def __init__(self, copytrade_manager=None, parent=None):
        super().__init__(parent)
        self.copytrade_manager = copytrade_manager
        self.setStyleSheet(PAGE_STYLE)
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
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "Data/Hora", "Master", "Ticket M", "Simbolo", "Acao",
            "Lote M", "Slave", "Ticket S", "Lote S", "Status"
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

        self.table.setRowCount(len(filtered))
        for i, row in enumerate(filtered):
            ts_val = row.get("timestamp", 0)
            ts = datetime.fromtimestamp(ts_val).strftime("%Y-%m-%d %H:%M:%S") if ts_val else ""
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
            ]
            for j, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                # Color code status
                if j == 9:
                    if val == "SUCCESS":
                        item.setForeground(Qt.green)
                    elif val == "FAILED":
                        item.setForeground(Qt.red)
                self.table.setItem(i, j, item)
