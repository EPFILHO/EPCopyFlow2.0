# gui/tabs/admin_tab.py
# Versão 1.0.9.n - Envio 1
# Objetivo: Implementar a aba Administrativo da interface MT5TraderGui.

from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QFormLayout, QPushButton, QLineEdit, QLabel
from PySide6.QtCore import Signal, Slot
import time

# Bloco 1 - Definição da Classe
class AdminTab(QWidget):
    command_requested = Signal(str, dict)  # Sinal: comando, payload

    def __init__(self, broker_combo, parent=None):
        super().__init__(parent)
        self.broker_combo = broker_combo
        self.setup_ui()

    # Bloco 2 - Configuração da UI
    def setup_ui(self):
        layout = QVBoxLayout(self)
        buttons_layout = QGridLayout()

        # Comandos administrativos
        admin_commands = [
            "PING", "GET_STATUS_INFO", "GET_BROKER_INFO", "GET_BROKER_SERVER",
            "GET_BROKER_PATH", "GET_ACCOUNT_INFO", "GET_ACCOUNT_BALANCE",
            "GET_ACCOUNT_LEVERAGE", "GET_ACCOUNT_FLAGS", "GET_ACCOUNT_MARGIN",
            "GET_ACCOUNT_STATE", "GET_TIME_SERVER", "POSITIONS", "ORDERS"
        ]
        self.admin_buttons = {}
        for i, cmd in enumerate(admin_commands):
            btn = QPushButton(cmd)
            btn.setMaximumWidth(150)
            btn.setStyleSheet("padding: 5px;")
            btn.clicked.connect(lambda checked, c=cmd: self.send_command(c))
            self.admin_buttons[cmd] = btn
            row = i // 3
            col = i % 3
            buttons_layout.addWidget(btn, row, col)

        # HISTORY_DATA
        history_data_layout = QFormLayout()
        self.history_data_symbol = QLineEdit("BTCUSD")
        self.history_data_timeframe = QLineEdit("H1")
        self.history_data_start = QLineEdit(str(int(time.time()) - 86400))
        self.history_data_end = QLineEdit(str(int(time.time())))
        self.history_data_symbol.setMaximumWidth(100)
        self.history_data_timeframe.setMaximumWidth(100)
        self.history_data_start.setMaximumWidth(100)
        self.history_data_end.setMaximumWidth(100)
        history_data_layout.addRow("Símbolo:", self.history_data_symbol)
        history_data_layout.addRow("Timeframe:", self.history_data_timeframe)
        history_data_layout.addRow("Start Time:", self.history_data_start)
        history_data_layout.addRow("End Time:", self.history_data_end)
        self.history_data_btn = QPushButton("HISTORY_DATA")
        self.history_data_btn.setMaximumWidth(150)
        self.history_data_btn.setStyleSheet("padding: 5px;")
        self.history_data_btn.clicked.connect(lambda: self.send_command("HISTORY_DATA"))
        history_data_layout.addRow("", self.history_data_btn)

        # HISTORY_TRADES
        history_trades_layout = QFormLayout()
        self.history_trades_start = QLineEdit(str(int(time.time()) - 86400))
        self.history_trades_end = QLineEdit(str(int(time.time())))
        self.history_trades_start.setMaximumWidth(100)
        self.history_trades_end.setMaximumWidth(100)
        history_trades_layout.addRow("Start Time:", self.history_trades_start)
        history_trades_layout.addRow("End Time:", self.history_trades_end)
        self.history_trades_btn = QPushButton("HISTORY_TRADES")
        self.history_trades_btn.setMaximumWidth(150)
        self.history_trades_btn.setStyleSheet("padding: 5px;")
        self.history_trades_btn.clicked.connect(lambda: self.send_command("HISTORY_TRADES"))
        history_trades_layout.addRow("", self.history_trades_btn)

        layout.addLayout(buttons_layout)
        layout.addLayout(history_data_layout)
        layout.addLayout(history_trades_layout)

    # Bloco 3 - Lógica de Comandos
    def send_command(self, command):
        payload = {}
        try:
            if command in ["PING", "GET_STATUS_INFO", "GET_BROKER_INFO", "GET_BROKER_SERVER",
                           "GET_BROKER_PATH", "GET_ACCOUNT_INFO", "GET_ACCOUNT_BALANCE",
                           "GET_ACCOUNT_LEVERAGE", "GET_ACCOUNT_FLAGS", "GET_ACCOUNT_MARGIN",
                           "GET_ACCOUNT_STATE", "GET_TIME_SERVER", "POSITIONS", "ORDERS"]:
                if command == "PING":
                    payload = {"timestamp": int(time.time())}
            elif command == "HISTORY_DATA":
                symbol = self.history_data_symbol.text()
                if not symbol:
                    self.command_requested.emit("ERROR", {"message": "Símbolo vazio"})
                    return
                timeframe = self.history_data_timeframe.text()
                if not timeframe:
                    self.command_requested.emit("ERROR", {"message": "Timeframe vazio"})
                    return
                start_time = int(self.history_data_start.text())
                end_time = int(self.history_data_end.text())
                if start_time >= end_time:
                    self.command_requested.emit("ERROR", {"message": "Start Time deve ser menor que End Time"})
                    return
                payload = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "start_time": start_time,
                    "end_time": end_time
                }
            elif command == "HISTORY_TRADES":
                start_time = int(self.history_trades_start.text())
                end_time = int(self.history_trades_end.text())
                if start_time >= end_time:
                    self.command_requested.emit("ERROR", {"message": "Start Time deve ser menor que End Time"})
                    return
                payload = {
                    "start_time": start_time,
                    "end_time": end_time
                }
            self.command_requested.emit(command, payload)
        except ValueError as e:
            self.command_requested.emit("ERROR", {"message": f"Erro nos parâmetros: {str(e)}"})

    # Bloco 4 - Atualização de Botões
    @Slot(bool)
    def update_buttons(self, is_registered):
        for btn in self.admin_buttons.values():
            btn.setEnabled(is_registered)
        self.history_data_btn.setEnabled(is_registered)
        self.history_trades_btn.setEnabled(is_registered)

# gui/tabs/admin_tab.py
# Versão 1.0.9.n - Envio 1