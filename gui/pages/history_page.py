# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# gui/pages/history_page.py
# Página de histórico de copytrades (leitura do SQLite).

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QDialog, QRadioButton, QDateEdit, QButtonGroup, QMessageBox
)
from PySide6.QtCore import Slot, Qt, QDate
from PySide6.QtGui import QColor
from datetime import datetime
from gui import themes

logger = logging.getLogger(__name__)


class ClearHistoryDialog(QDialog):
    """Diálogo para escolher o escopo da limpeza do histórico."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Limpar Histórico")
        self.setMinimumWidth(360)
        self.setStyleSheet(themes.brokers_dialog_style())
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.radio_all = QRadioButton("Limpar tudo")
        self.radio_all.setChecked(True)
        self.radio_range = QRadioButton("Limpar por intervalo de datas")
        group = QButtonGroup(self)
        group.addButton(self.radio_all)
        group.addButton(self.radio_range)
        layout.addWidget(self.radio_all)
        layout.addWidget(self.radio_range)

        date_row = QHBoxLayout()
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("dd/MM/yyyy")
        self.date_from.setDate(QDate.currentDate())
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("dd/MM/yyyy")
        self.date_to.setDate(QDate.currentDate())
        date_row.addWidget(QLabel("De:"))
        date_row.addWidget(self.date_from)
        date_row.addWidget(QLabel("Até:"))
        date_row.addWidget(self.date_to)
        date_row.addStretch()
        layout.addLayout(date_row)

        self.radio_range.toggled.connect(self._on_mode_changed)
        self._on_mode_changed(False)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Limpar")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_mode_changed(self, range_selected):
        self.date_from.setEnabled(range_selected)
        self.date_to.setEnabled(range_selected)

    def get_range(self):
        """Retorna (start_ts, end_ts). (None, None) = limpar tudo."""
        if self.radio_all.isChecked():
            return None, None
        d_from = self.date_from.date().toPython()
        d_to = self.date_to.date().toPython()
        start_ts = datetime(d_from.year, d_from.month, d_from.day,
                            0, 0, 0).timestamp()
        end_ts = datetime(d_to.year, d_to.month, d_to.day,
                          23, 59, 59).timestamp()
        return start_ts, end_ts


class HistoryPage(QWidget):
    def __init__(self, copytrade_manager=None, parent=None):
        super().__init__(parent)
        self.copytrade_manager = copytrade_manager
        self.setStyleSheet(themes.history_page_style())
        self._init_ui()
        if self.copytrade_manager is not None:
            self.copytrade_manager.trade_history_ready.connect(self._on_history_ready)
            self.copytrade_manager.history_cleared.connect(self._on_history_cleared)

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

        clear_btn = QPushButton("Limpar Historico")
        clear_btn.setProperty("class", "action-btn")
        clear_btn.clicked.connect(self._on_clear_clicked)
        header.addWidget(clear_btn)

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
        # ResizeToContents + sem stretch da última seção: as colunas mantêm
        # sua largura natural e a tabela exibe barra de rolagem horizontal
        # quando não cabe na largura disponível (monitores menores).
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table, 1)

    def _on_clear_clicked(self):
        dialog = ClearHistoryDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        start_ts, end_ts = dialog.get_range()
        if start_ts is None and end_ts is None:
            msg = "Apagar TODO o histórico de copytrades?"
        else:
            d_from = datetime.fromtimestamp(start_ts).strftime("%d/%m/%Y")
            d_to = datetime.fromtimestamp(end_ts).strftime("%d/%m/%Y")
            msg = f"Apagar o histórico de {d_from} até {d_to}?"
        reply = QMessageBox.question(
            self, "Confirmação", msg, QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes and self.copytrade_manager:
            self.copytrade_manager.clear_trade_history(start_ts, end_ts)

    @Slot(int)
    def _on_history_cleared(self, deleted):
        QMessageBox.information(
            self, "Histórico limpo",
            f"{deleted} registro(s) removido(s)."
        )
        self.refresh()

    @Slot()
    def refresh(self, _data=None):
        if not self.copytrade_manager:
            return
        self.copytrade_manager.request_trade_history(limit=200)

    @Slot(list, str)
    def _on_history_ready(self, rows, broker_key_filter):
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
