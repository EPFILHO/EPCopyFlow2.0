# gui/tabs/trading_tab.py
# Versão 1.0.9.n - Envio 1
# Objetivo: Implementar a aba Trading da interface MT5TraderGui.

from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QFormLayout, QPushButton, QLineEdit, QLabel
from PySide6.QtCore import Signal, Slot

# Bloco 1 - Definição da Classe
class TradingTab(QWidget):
    command_requested = Signal(str, dict)  # Sinal: comando, payload

    def __init__(self, broker_combo, parent=None):
        super().__init__(parent)
        self.broker_combo = broker_combo
        self.setup_ui()

    # Bloco 2 - Configuração da UI
    def setup_ui(self):
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.trade_symbol = QLineEdit("BTCUSD")
        self.trade_volume = QLineEdit("0.1")
        self.trade_price = QLineEdit("0.0")
        self.trade_sl = QLineEdit("0.0")
        self.trade_tp = QLineEdit("0.0")
        self.trade_deviation = QLineEdit("10")
        self.trade_comment = QLineEdit("Teste GUI")
        self.trade_symbol.setMaximumWidth(100)
        self.trade_volume.setMaximumWidth(100)
        self.trade_price.setMaximumWidth(100)
        self.trade_sl.setMaximumWidth(100)
        self.trade_tp.setMaximumWidth(100)
        self.trade_deviation.setMaximumWidth(100)
        self.trade_comment.setMaximumWidth(100)
        form_layout.addRow("Símbolo:", self.trade_symbol)
        form_layout.addRow("Volume:", self.trade_volume)
        form_layout.addRow("Preço:", self.trade_price)
        form_layout.addRow("SL:", self.trade_sl)
        form_layout.addRow("TP:", self.trade_tp)
        form_layout.addRow("Deviation:", self.trade_deviation)
        form_layout.addRow("Comentário:", self.trade_comment)

        buttons_layout = QGridLayout()
        trade_commands = [
            "ORDER_TYPE_BUY", "ORDER_TYPE_SELL",
            "ORDER_TYPE_BUY_LIMIT", "ORDER_TYPE_SELL_LIMIT",
            "ORDER_TYPE_BUY_STOP", "ORDER_TYPE_SELL_STOP"
        ]
        self.trade_buttons = {}
        for i, cmd in enumerate(trade_commands):
            btn = QPushButton(cmd)
            btn.setMaximumWidth(150)
            btn.setStyleSheet("padding: 5px;")
            btn.clicked.connect(lambda checked, c=f"TRADE_{cmd}": self.send_command(c))
            self.trade_buttons[cmd] = btn
            row = i // 3
            col = i % 3
            buttons_layout.addWidget(btn, row, col)

        modify_layout = QFormLayout()
        self.trade_ticket = QLineEdit("0")
        self.modify_sl = QLineEdit("0.0")
        self.modify_tp = QLineEdit("0.0")
        self.partial_volume = QLineEdit("0.1")
        self.close_symbol = QLineEdit("BTCUSD")
        self.trade_ticket.setMaximumWidth(100)
        self.modify_sl.setMaximumWidth(100)
        self.modify_tp.setMaximumWidth(100)
        self.partial_volume.setMaximumWidth(100)
        self.close_symbol.setMaximumWidth(100)
        modify_layout.addRow("Ticket (Modificar/Fechar):", self.trade_ticket)
        modify_layout.addRow("SL (Modificar):", self.modify_sl)
        modify_layout.addRow("TP (Modificar):", self.modify_tp)
        modify_layout.addRow("Volume Parcial:", self.partial_volume)
        modify_layout.addRow("Símbolo (Fechar):", self.close_symbol)

        modify_buttons_layout = QGridLayout()
        modify_commands = [
            "POSITION_MODIFY", "POSITION_PARTIAL",
            "POSITION_CLOSE_ID", "POSITION_CLOSE_SYMBOL",
            "ORDER_MODIFY", "ORDER_CANCEL"
        ]
        self.modify_buttons = {}
        for i, cmd in enumerate(modify_commands):
            btn = QPushButton(cmd)
            btn.setMaximumWidth(150)
            btn.setStyleSheet("padding: 5px;")
            btn.clicked.connect(lambda checked, c=f"TRADE_{cmd}": self.send_command(c))
            self.modify_buttons[cmd] = btn
            row = i // 3
            col = i % 3
            modify_buttons_layout.addWidget(btn, row, col)

        layout.addLayout(form_layout)
        layout.addLayout(buttons_layout)
        layout.addLayout(modify_layout)
        layout.addLayout(modify_buttons_layout)

    # Bloco 3 - Lógica de Comandos
    def send_command(self, command):
        payload = {}
        try:
            if command in [
                "TRADE_ORDER_TYPE_BUY", "TRADE_ORDER_TYPE_SELL",
                "TRADE_ORDER_TYPE_BUY_LIMIT", "TRADE_ORDER_TYPE_SELL_LIMIT",
                "TRADE_ORDER_TYPE_BUY_STOP", "TRADE_ORDER_TYPE_SELL_STOP"
            ]:
                symbol = self.trade_symbol.text()
                if not symbol:
                    self.command_requested.emit("ERROR", {"message": "Símbolo vazio"})
                    return
                volume = float(self.trade_volume.text()) if self.trade_volume.text() else 0.0
                if volume <= 0:
                    self.command_requested.emit("ERROR", {"message": "Volume inválido"})
                    return
                price = float(self.trade_price.text()) if self.trade_price.text() else 0.0
                sl = float(self.trade_sl.text()) if self.trade_sl.text() else 0.0
                tp = float(self.trade_tp.text()) if self.trade_tp.text() else 0.0
                deviation = int(self.trade_deviation.text()) if self.trade_deviation.text() else 10
                payload = {
                    "symbol": symbol,
                    "volume": volume,
                    "price": price,
                    "sl": sl,
                    "tp": tp,
                    "deviation": deviation,
                    "comment": self.trade_comment.text()
                }
            elif command == "TRADE_POSITION_MODIFY":
                ticket = int(self.trade_ticket.text())
                if ticket <= 0:
                    self.command_requested.emit("ERROR", {"message": "Ticket inválido"})
                    return
                sl = float(self.modify_sl.text()) if self.modify_sl.text() else 0.0
                tp = float(self.modify_tp.text()) if self.modify_tp.text() else 0.0
                payload = {"ticket": ticket, "sl": sl, "tp": tp}
            elif command == "TRADE_POSITION_PARTIAL":
                ticket = int(self.trade_ticket.text())
                if ticket <= 0:
                    self.command_requested.emit("ERROR", {"message": "Ticket inválido"})
                    return
                volume = float(self.partial_volume.text()) if self.partial_volume.text() else 0.0
                if volume <= 0:
                    self.command_requested.emit("ERROR", {"message": "Volume parcial inválido"})
                    return
                payload = {"ticket": ticket, "volume": volume}
            elif command in ["TRADE_POSITION_CLOSE_ID", "TRADE_ORDER_CANCEL"]:
                ticket = int(self.trade_ticket.text())
                if ticket <= 0:
                    self.command_requested.emit("ERROR", {"message": "Ticket inválido"})
                    return
                payload = {"ticket": ticket}
            elif command == "TRADE_POSITION_CLOSE_SYMBOL":
                symbol = self.close_symbol.text()
                if not symbol:
                    self.command_requested.emit("ERROR", {"message": "Símbolo vazio"})
                    return
                payload = {"symbol": symbol}
                command = "TRADE_POSITION_CLOSE"
            elif command == "TRADE_ORDER_MODIFY":
                ticket = int(self.trade_ticket.text())
                if ticket <= 0:
                    self.command_requested.emit("ERROR", {"message": "Ticket inválido"})
                    return
                price = float(self.trade_price.text()) if self.trade_price.text() else 0.0
                sl = float(self.modify_sl.text()) if self.modify_sl.text() else 0.0
                tp = float(self.modify_tp.text()) if self.modify_tp.text() else 0.0
                payload = {"ticket": ticket, "price": price, "sl": sl, "tp": tp}
            self.command_requested.emit(command, payload)
        except ValueError as e:
            self.command_requested.emit("ERROR", {"message": f"Erro nos parâmetros: {str(e)}"})

    # Bloco 4 - Atualização de Botões
    @Slot(bool)
    def update_buttons(self, is_registered):
        for btn in self.trade_buttons.values():
            btn.setEnabled(is_registered)
        for btn in self.modify_buttons.values():
            btn.setEnabled(is_registered)

# gui/tabs/trading_tab.py
# Versão 1.0.9.n - Envio 1