# gui/tabs/indicators_tab.py
# Versão 1.0.9.r - Envio 1
# Objetivo: Implementar a aba Indicadores da interface MT5TraderGui.
# Updated: 2025-07-07 for Version 1.0.9.r to add HISTORY_DATA command with indicators support.
# Ajustes:
# - [1.0.9.r - Envio 1] Adicionado suporte para comando HISTORY_DATA com indicadores, incluindo nova seção na UI para configurar símbolo, timeframe, período inicial/final e indicadores.
# - [1.0.9.n - Envio 1] Versão base com suporte para START_STREAM_OHLC_INDICATORS, STOP_STREAM_OHLC_INDICATORS, GET_INDICATOR_MA, GET_OHLC, GET_TICK.

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton, QLineEdit, QLabel, QTableWidget, QTableWidgetItem, QHeaderView
from PySide6.QtCore import Signal, Slot
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Bloco 1 - Definição da Classe
class IndicatorsTab(QWidget):
    command_requested = Signal(str, dict, bool)  # Sinal: comando, payload, use_data_port

    def __init__(self, broker_combo, parent=None):
        super().__init__(parent)
        self.broker_combo = broker_combo
        self.setup_ui()

    # Bloco 2 - Configuração da UI
    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Tabela de streaming
        layout.addWidget(QLabel("Configuração de Streaming OHLC + Indicadores:"))
        self.stream_table = QTableWidget()
        self.stream_table.setColumnCount(3)
        self.stream_table.setHorizontalHeaderLabels(["Símbolo", "Timeframe", "Indicadores (ex: MA,9;MA,21)"])
        self.stream_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stream_table.setRowCount(1)
        self.stream_table.setItem(0, 0, QTableWidgetItem("EURUSD"))
        self.stream_table.setItem(0, 1, QTableWidgetItem("M1"))
        self.stream_table.setItem(0, 2, QTableWidgetItem("MA,9;MA,21"))
        layout.addWidget(self.stream_table)

        # Botões Adicionar/Remover
        table_row_buttons_layout = QHBoxLayout()
        add_row_btn = QPushButton("Adicionar Ativo")
        add_row_btn.clicked.connect(self.add_stream_row)
        remove_row_btn = QPushButton("Remover Ativo")
        remove_row_btn.clicked.connect(self.remove_stream_row)
        table_row_buttons_layout.addWidget(add_row_btn)
        table_row_buttons_layout.addWidget(remove_row_btn)
        layout.addLayout(table_row_buttons_layout)

        # Botões START/STOP
        stream_control_buttons_layout = QHBoxLayout()
        self.start_stream_indicators_btn = QPushButton("START_STREAM_OHLC_INDICATORS")
        self.start_stream_indicators_btn.setMaximumWidth(250)
        self.start_stream_indicators_btn.setStyleSheet("padding: 5px;")
        self.start_stream_indicators_btn.clicked.connect(lambda: self.send_command("START_STREAM_OHLC_INDICATORS"))
        stream_control_buttons_layout.addWidget(self.start_stream_indicators_btn)

        self.stop_stream_indicators_btn = QPushButton("STOP_STREAM_OHLC_INDICATORS")
        self.stop_stream_indicators_btn.setMaximumWidth(250)
        self.stop_stream_indicators_btn.setStyleSheet("padding: 5px;")
        self.stop_stream_indicators_btn.clicked.connect(lambda: self.send_command("STOP_STREAM_OHLC_INDICATORS"))
        stream_control_buttons_layout.addWidget(self.stop_stream_indicators_btn)
        layout.addLayout(stream_control_buttons_layout)

        # Comandos single-shot
        layout.addWidget(QLabel("\nComandos de Indicadores (Single-Shot):"))
        indicators_form_layout = QFormLayout()
        self.indicator_symbol = QLineEdit("EURUSD")
        self.indicator_timeframe = QLineEdit("H1")
        self.indicator_period = QLineEdit("20")
        self.indicator_symbol.setMaximumWidth(100)
        self.indicator_timeframe.setMaximumWidth(100)
        self.indicator_period.setMaximumWidth(100)
        indicators_form_layout.addRow("Símbolo:", self.indicator_symbol)
        indicators_form_layout.addRow("Timeframe:", self.indicator_timeframe)
        indicators_form_layout.addRow("Período (MA):", self.indicator_period)
        layout.addLayout(indicators_form_layout)

        # Nova seção para HISTORY_DATA
        layout.addWidget(QLabel("\nComando HISTORY_DATA com Indicadores:"))
        history_form_layout = QFormLayout()
        self.history_symbol = QLineEdit("EURUSD")
        self.history_timeframe = QLineEdit("H1")
        self.history_indicators = QLineEdit("MA,9;MA,21")
        self.history_symbol.setMaximumWidth(100)
        self.history_timeframe.setMaximumWidth(100)
        self.history_indicators.setMaximumWidth(200)
        history_form_layout.addRow("Símbolo:", self.history_symbol)
        history_form_layout.addRow("Timeframe:", self.history_timeframe)
        history_form_layout.addRow("Indicadores (ex: MA,9;MA,21):", self.history_indicators)
        layout.addLayout(history_form_layout)

        single_shot_buttons_layout = QHBoxLayout()
        indicator_commands = ["GET_INDICATOR_MA", "GET_OHLC", "GET_TICK", "HISTORY_DATA"]
        self.indicator_buttons = {}
        for cmd in indicator_commands:
            btn = QPushButton(cmd)
            btn.setMaximumWidth(150)
            btn.setStyleSheet("padding: 5px;")
            btn.clicked.connect(lambda checked, c=cmd: self.send_command(c))
            self.indicator_buttons[cmd] = btn
            single_shot_buttons_layout.addWidget(btn)
        layout.addLayout(single_shot_buttons_layout)

    # Bloco 3 - Gerenciamento da Tabela
    def add_stream_row(self):
        row = self.stream_table.rowCount()
        self.stream_table.insertRow(row)
        self.stream_table.setItem(row, 0, QTableWidgetItem("EURUSD"))
        self.stream_table.setItem(row, 1, QTableWidgetItem("M1"))
        self.stream_table.setItem(row, 2, QTableWidgetItem("MA,9;MA,21"))

    def remove_stream_row(self):
        row = self.stream_table.currentRow()
        if row >= 0:
            self.stream_table.removeRow(row)
        else:
            self.command_requested.emit("ERROR", {"message": "Selecione uma linha para remover."})

    # Bloco 4 - Lógica de Comandos
    def send_command(self, command):
        payload = {}
        use_data_port = True
        try:
            broker_key = self.broker_combo.currentText()
            if not broker_key:
                self.command_requested.emit("ERROR", {"message": "Nenhuma corretora selecionada."})
                return

            if command == "START_STREAM_OHLC_INDICATORS":
                configs = []
                for row in range(self.stream_table.rowCount()):
                    symbol_item = self.stream_table.item(row, 0)
                    timeframe_item = self.stream_table.item(row, 1)
                    indicators_item = self.stream_table.item(row, 2)
                    if symbol_item and timeframe_item and indicators_item and \
                            symbol_item.text() and timeframe_item.text() and indicators_item.text():
                        symbol = symbol_item.text()
                        timeframe = timeframe_item.text()
                        indicators_str = indicators_item.text()
                        indicators = []
                        for ind_entry in indicators_str.split(';'):
                            if ind_entry:
                                try:
                                    type_, period = ind_entry.split(',')
                                    indicators.append({"type": type_.strip(), "period": int(period.strip())})
                                except ValueError:
                                    self.command_requested.emit("ERROR", {
                                        "message": f"Formato inválido nos indicadores '{ind_entry}' da linha {row + 1}. Use 'TIPO,PERIODO'."
                                    })
                                    return
                            else:
                                self.command_requested.emit("ERROR", {
                                    "message": f"Indicador vazio na linha {row + 1}. Ignorando."
                                })
                        if not indicators:
                            self.command_requested.emit("ERROR", {
                                "message": f"Nenhum indicador válido na linha {row + 1}. Verifique o formato."
                            })
                            return
                        configs.append({
                            "symbol": symbol.strip(),
                            "timeframe": timeframe.strip(),
                            "indicators": indicators
                        })
                    else:
                        self.command_requested.emit("ERROR", {
                            "message": f"Linha {row + 1} da tabela de streaming incompleta ou vazia. Ignorando."
                        })
                if not configs:
                    self.command_requested.emit("ERROR", {"message": "Nenhuma configuração de streaming válida."})
                    return
                payload = {"configs": configs}
            elif command == "STOP_STREAM_OHLC_INDICATORS":
                payload = {}
            elif command == "HISTORY_DATA":
                symbol = self.history_symbol.text()
                timeframe = self.history_timeframe.text()
                indicators_str = self.history_indicators.text()
                if not symbol or not timeframe:
                    self.command_requested.emit("ERROR", {"message": "Símbolo ou timeframe vazio para HISTORY_DATA."})
                    return
                indicators = []
                if indicators_str:
                    for ind_entry in indicators_str.split(';'):
                        if ind_entry:
                            try:
                                type_, period = ind_entry.split(',')
                                indicators.append({"type": type_.strip(), "period": int(period.strip())})
                            except ValueError:
                                self.command_requested.emit("ERROR", {
                                    "message": f"Formato inválido nos indicadores '{ind_entry}' para HISTORY_DATA. Use 'TIPO,PERIODO'."
                                })
                                return
                # Definir período de 7 dias para teste
                end_time = int(datetime.now().timestamp())
                start_time = int((datetime.now() - timedelta(days=1)).timestamp())
                payload = {
                    "broker_key": broker_key,
                    "symbol": symbol.strip(),
                    "timeframe": timeframe.strip(),
                    "start_time": start_time,
                    "end_time": end_time,
                    "indicators": indicators
                }
            elif command in ["GET_INDICATOR_MA", "GET_OHLC", "GET_TICK"]:
                symbol = self.indicator_symbol.text()
                if not symbol:
                    self.command_requested.emit("ERROR", {"message": "Símbolo vazio"})
                    return
                payload = {"symbol": symbol}
                if command in ["GET_INDICATOR_MA", "GET_OHLC"]:
                    timeframe = self.indicator_timeframe.text()
                    if not timeframe:
                        self.command_requested.emit("ERROR", {"message": "Timeframe vazio"})
                        return
                    payload["timeframe"] = timeframe
                if command == "GET_INDICATOR_MA":
                    period = int(self.indicator_period.text())
                    if period <= 0:
                        self.command_requested.emit("ERROR", {"message": "Período inválido"})
                        return
                    payload["period"] = period
            self.command_requested.emit(command, payload, use_data_port)
            logger.info(f"Comando {command} enviado com payload: {payload}")
        except Exception as e:
            self.command_requested.emit("ERROR", {"message": f"Erro ao processar comando {command}: {str(e)}"})
            logger.error(f"Erro ao processar comando {command}: {str(e)}")

    # Bloco 5 - Atualização de Botões
    @Slot(bool, bool)
    def update_buttons(self, is_registered, streaming_active):
        for btn in self.indicator_buttons.values():
            btn.setEnabled(is_registered)
        self.start_stream_indicators_btn.setEnabled(is_registered and not streaming_active)
        self.stop_stream_indicators_btn.setEnabled(is_registered and streaming_active)

# gui/tabs/indicators_tab.py
# Versão 1.0.9.r - Envio 1