# gui/tabs/charts_tab.py
# Versão: 1.0.9.r - Envio 15
# Author: [EPFilho]
# Objetivo: Implementar a aba Gráficos para exibir gráficos de ativos monitorados com dados OHLC e indicadores, salvando e carregando configurações de indicadores.
# Updated: 2025-07-09 for Version 1.0.9.r - Envio 16 to:
#   - Corrigido: Erro "kwarg addplot validator returned False" quando não há indicadores para plotar (ex.: apenas RSI com subjanela Volume)
#   - Adicionado: Legendas para médias móveis (ex.: MA_9, EMA_20) na janela principal usando label em add_plots
#   - Corrigido: Médias móveis (ex.: MA, EMA) agora são plotadas sempre na janela principal, independentemente da seleção de Volume ou RSI na subjanela
#   - Corrigido: RSI agora é plotado apenas na subjanela (panel=1) quando "RSI" é selecionado; outros indicadores (ex.: MA, EMA) são sempre plotados na janela principal
#   - Adicionada combobox para alternar entre Volume e RSI na subjanela
#   - Modificado IndicatorStyleDialog para usar descrições amigáveis ("Cheia", "Tracejada", "Pontilhada", "Traço-Ponto") em vez de símbolos
#   - Definido estilo gráfico padrão como "default"
# Ajustes anteriores:
# - [1.0.9.r - Envio 11] Modificado IndicatorStyleDialog para carregar configurações de ChartsTab.indicator_styles no diálogo.
# - [1.0.9.r - Envio 10] Modificado _choose_color para definir color_name="#FF0000" quando cor inválida; removido setMinimumWidth; reorganizado controles em duas linhas.
# - [1.0.9.r - Envio 9] Adicionado rastreamento de estilos (self._last_indicator_styles) para replotagem imediata; cor padrão #FF0000 no diálogo; largura mínima da GUI definida como 1600px.
# - [1.0.9.r - Envio 8] Corrigido parâmetro style em mpf.plot para usar self.style_combo.currentText().
# - [1.0.9.r - Envio 7] Adicionado diálogo para personalizar cores, estilos de linha e espessura de indicadores.
# - [1.0.9.r - Envio 6] Alterado linestyle para '-' (contínuo) e adicionado width=0.8 para linhas de indicadores; incluídos comentários.
# - [1.0.9.r - Envio 5] Normalização de indicadores de '' para 'indicators' em process_historical_data.
# - [1.0.9.r - Envio 4] Forçar atualização do gráfico após dados históricos; logs detalhados para indicadores.
# - [1.0.9.r - Envio 3] Inclusão de indicadores no payload de HISTORY_DATA.
# - [1.0.9.r - Envio 2] Suporte para indicadores via HISTORY_DATA e OHLC_INDICATOR_UPDATE; validação relaxada.
# - [1.0.9.q - Envio 1] Relaxed validation in update_stream_data to accept timestamp_mql=0 if ohlc['time'] is valid.
# - [1.0.9.p - Envio 55] Reintroduzido temporizador fallback; verificação de dados; logs melhorados.
# - [1.0.9.p - Envio 54] Ajustado formato de data no eixo X; excluído candle em formação; atualização imediata via stream.

import pandas as pd
import logging
import time
from datetime import datetime
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QPushButton, QDialog, QFormLayout, \
    QLineEdit, QColorDialog
from PySide6.QtCore import Slot, Signal, QEvent, QTimer
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import mplfinance as mpf
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

class IndicatorStyleDialog(QDialog):
    def __init__(self, indicators, indicator_styles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurar Estilo de Indicadores")
        self.indicators = indicators
        self.indicator_styles = indicator_styles  # Configurações existentes de ChartsTab
        self.styles = {}
        self.setup_ui()

    def setup_ui(self):
        layout = QFormLayout(self)
        self.inputs = {}
        for ind in self.indicators:
            # Carregar configurações existentes ou usar padrões
            default_style = self.indicator_styles.get(ind, {
                "color": "#FF0000",
                "linestyle": "-",
                "width": 0.8
            })
            self.inputs[ind] = {
                "color": QPushButton("Escolher Cor"),
                "linestyle": QComboBox(),
                "width": QLineEdit(str(default_style["width"]))
            }
            self.inputs[ind]["linestyle"].addItems(["Cheia", "Tracejada", "Pontilhada", "Traço-Ponto"])
            linestyle_mapping = {
                "-": "Cheia",
                "--": "Tracejada",
                ":": "Pontilhada",
                "-.": "Traço-Ponto"
            }
            self.inputs[ind]["linestyle"].setCurrentText(linestyle_mapping.get(default_style["linestyle"], "Cheia"))
            self.inputs[ind]["color"].clicked.connect(lambda checked, ind=ind: self._choose_color(ind))
            self.inputs[ind]["color_name"] = default_style["color"]
            self.inputs[ind]["color"].setStyleSheet(f"background-color: {default_style['color']}")
            layout.addRow(QLabel(f"{ind} - Cor:"), self.inputs[ind]["color"])
            layout.addRow(QLabel(f"{ind} - Estilo de Linha:"), self.inputs[ind]["linestyle"])
            layout.addRow(QLabel(f"{ind} - Espessura:"), self.inputs[ind]["width"])

        accept_button = QPushButton("Aplicar")
        accept_button.clicked.connect(self.accept)
        layout.addRow(accept_button)

    def _choose_color(self, indicator):
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            self.inputs[indicator]["color"].setStyleSheet(f"background-color: {color.name()}")
            self.inputs[indicator]["color_name"] = color.name()
        else:
            self.inputs[indicator]["color_name"] = "#FF0000"  # Vermelho vivo como padrão se cancelado

    def get_styles(self):
        styles = {}
        linestyle_mapping = {
            "Cheia": "-",
            "Tracejada": "--",
            "Pontilhada": ":",
            "Traço-Ponto": "-."
        }
        for ind in self.indicators:
            try:
                width = float(self.inputs[ind]["width"].text())
                if width <= 0:
                    logger.warning(f"Espessura inválida para {ind}: {width}, usando padrão 0.8")
                    width = 0.8
            except ValueError:
                logger.warning(f"Espessura inválida para {ind}: {self.inputs[ind]['width'].text()}, usando padrão 0.8")
                width = 0.8
            color = self.inputs[ind].get("color_name", "#FF0000")  # Vermelho vivo como padrão
            styles[ind] = {
                "color": color,
                "linestyle": linestyle_mapping.get(self.inputs[ind]["linestyle"].currentText(), "-"),
                "width": width
            }
        return styles

class ChartsTab(QWidget):
    error_occurred = Signal(str)
    command_requested = Signal(str, dict)

    def __init__(self, broker_combo, zmq_message_handler, parent=None):
        super().__init__(parent)
        self.broker_combo = broker_combo
        self.zmq_message_handler = zmq_message_handler
        self.chart_data = {}  # {broker_key: {symbol: {timeframe: DataFrame}}}
        self.broker_time_offsets = {}  # {broker_key: time_diff_seconds}
        self.paused = False
        self.canvas = None
        self.figure = None
        self.buffer = {}  # {broker_key: {symbol: {timeframe: list of candles}}}
        self.historical_loaded = {}  # {broker_key: {symbol: {timeframe: bool}}}
        self.historical_requested = {}  # {broker_key: {symbol: {timeframe: bool}}}
        self.monitored_pairs = {}  # {broker_key: {symbol: {timeframe: {"timeframes": set(), "indicators": list}}}}
        self.timers = {}  # {broker_key: {symbol: {timeframe: QTimer}}}
        self.first_stream = {}  # {broker_key: bool}
        self.is_updating_chart = False
        self._is_plotting = False
        self._last_chart_key = None
        self._last_data_length = {}  # {broker_key_symbol_timeframe: int}
        self._last_candle_hash = {}  # {broker_key_symbol_timeframe: str}
        self.indicator_styles = {}  # {ind_key: {"color": str, "linestyle": str, "width": float}}
        self._last_indicator_styles = {}  # Rastrear estilos anteriores para detectar mudanças
        self.setup_ui()
        self._populate_combos()
        self._connect_signals()
        logger.debug("ChartsTab inicializado com sucesso")

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        controls_layout = QHBoxLayout()
        self.symbol_combo = QComboBox()
        self.timeframe_combo = QComboBox()
        self.candles_combo = QComboBox()
        self.candles_combo.addItems(["20", "40", "60", "80", "100"])
        self.candles_combo.setCurrentText("100")
        controls_layout.addWidget(QLabel("Ativo:"))
        controls_layout.addWidget(self.symbol_combo)
        controls_layout.addWidget(QLabel("Timeframe:"))
        controls_layout.addWidget(self.timeframe_combo)
        controls_layout.addWidget(QLabel("Número de Candles:"))
        controls_layout.addWidget(self.candles_combo)
        self.main_layout.addLayout(controls_layout)

        controls_layout_lower = QHBoxLayout()
        self.style_combo = QComboBox()
        self.type_combo = QComboBox()
        self.indicator_style_button = QPushButton("Configurar Indicadores")
        self.style_combo.addItems(
            ['binance', 'binancedark', 'blueskies', 'brasil', 'charles', 'checkers', 'classic', 'default', 'ibd',
             'kenan', 'mike', 'nightclouds', 'sas', 'starsandstripes', 'tradingview', 'yahoo'])
        self.style_combo.setCurrentText("default")
        self.type_combo.addItems(["candle", "ohlc", "line"])
        self.type_combo.setCurrentText("candle")
        controls_layout_lower.addWidget(QLabel("Estilo:"))
        controls_layout_lower.addWidget(self.style_combo)
        controls_layout_lower.addWidget(QLabel("Tipo:"))
        controls_layout_lower.addWidget(self.type_combo)
        controls_layout_lower.addWidget(self.indicator_style_button)
        self.rsi_volume_combo = QComboBox()
        self.rsi_volume_combo.addItems(["Volume", "RSI"])
        self.rsi_volume_combo.setCurrentText("Volume")
        controls_layout_lower.addWidget(QLabel("Subjanela:"))
        controls_layout_lower.addWidget(self.rsi_volume_combo)
        self.main_layout.addLayout(controls_layout_lower)

        self.chart_widget = QWidget()
        self.main_layout.addWidget(self.chart_widget)
        self.chart_layout = QVBoxLayout(self.chart_widget)
        self.indicator_style_button.clicked.connect(self._open_indicator_style_dialog)
        self.rsi_volume_combo.currentTextChanged.connect(self.update_chart)
        logger.debug("Interface gráfica configurada com sucesso")

    def _open_indicator_style_dialog(self):
        broker_key = self.broker_combo.currentText()
        symbol = self.symbol_combo.currentText()
        timeframe = self.timeframe_combo.currentText()
        if not (broker_key in self.chart_data and symbol in self.chart_data[broker_key] and
                timeframe in self.chart_data[broker_key][symbol]):
            logger.warning("Nenhum dado disponível para configurar indicadores")
            self.error_occurred.emit("Erro: Selecione um ativo e timeframe com dados")
            return
        df = self.chart_data[broker_key][symbol][timeframe]
        indicator_columns = [col for col in df.columns if col not in ["Date", "Open", "High", "Low", "Close", "Volume"]]
        if not indicator_columns:
            logger.warning("Nenhum indicador disponível para configuração")
            self.error_occurred.emit("Erro: Nenhum indicador disponível")
            return
        dialog = IndicatorStyleDialog(indicator_columns, self.indicator_styles, self)
        if dialog.exec():
            self.indicator_styles.update(dialog.get_styles())
            logger.debug(f"Estilos de indicadores atualizados: {self.indicator_styles}")
            self.update_chart()

    def _connect_signals(self):
        self.zmq_message_handler.time_server_received.connect(self._update_broker_time_offset)
        self.zmq_message_handler.stream_ohlc_indicators_received.connect(self._update_monitored_pairs)
        self.zmq_message_handler.stream_ohlc_received.connect(self._request_server_time)
        self.zmq_message_handler.history_data_received.connect(self.process_historical_data)
        self.broker_combo.currentTextChanged.connect(self._update_symbol_combo)
        self.symbol_combo.currentTextChanged.connect(self._update_timeframe_combo)
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_timeframe_changed)
        self.timeframe_combo.currentTextChanged.connect(self._on_symbol_timeframe_changed)
        self.zmq_message_handler.stream_ohlc_indicators_received.connect(self.update_stream_data)
        self.candles_combo.currentTextChanged.connect(self.update_chart)
        self.style_combo.currentTextChanged.connect(self.update_chart)
        self.type_combo.currentTextChanged.connect(self.update_chart)
        logger.debug("Sinais conectados com sucesso")

    @Slot(dict)
    def _request_server_time(self, data):
        broker_key = data.get("broker_key", None)
        if isinstance(broker_key,
                      str) and broker_key and broker_key not in self.broker_time_offsets and self.first_stream.get(
            broker_key, True):
            self.command_requested.emit("GET_TIME_SERVER", {"broker_key": broker_key})
            logger.debug(f"Solicitado GET_TIME_SERVER para {broker_key} na primeira stream")
            self.first_stream[broker_key] = False
            QTimer.singleShot(5000, lambda: self._retry_server_time(broker_key))
        else:
            logger.debug(f"Ignorado GET_TIME_SERVER: broker_key={broker_key} inválido ou já solicitado")

    def _retry_server_time(self, broker_key):
        if broker_key not in self.broker_time_offsets:
            logger.warning(f"Retry de GET_TIME_SERVER para {broker_key} após 5 segundos")
            self.command_requested.emit("GET_TIME_SERVER", {"broker_key": broker_key})
            self.first_stream[broker_key] = False

    @Slot(dict)
    def _update_broker_time_offset(self, time_server):
        broker_key = time_server.get("broker_key", None)
        server_time = time_server.get("time_server", None)
        if broker_key and server_time and isinstance(broker_key, str):
            try:
                self.broker_time_offsets[broker_key] = float(server_time)
                logger.debug(f"Tempo do servidor armazenado para {broker_key}: {server_time}")
                if broker_key == self.broker_combo.currentText():
                    self.update_chart()
            except (ValueError, TypeError) as e:
                logger.error(f"Erro ao processar tempo do servidor para {broker_key}: {str(e)}")
                self.broker_time_offsets[broker_key] = 0
                logger.warning(f"Offset de tempo padrão (0 segundos) usado para {broker_key}")
        else:
            logger.error(f"Dados inválidos para time_server: broker_key={broker_key}, time_server={server_time}")
            if broker_key:
                self.broker_time_offsets[broker_key] = 0
                self.error_occurred.emit(f"Erro: Tempo do servidor inválido para {broker_key}")
                logger.warning(f"Offset de tempo padrão (0 segundos) usado para {broker_key}")

    def _update_monitored_pairs(self, data):
        logger.debug(f"_update_monitored_pairs chamado com data: {data}")
        broker_key = data.get("broker_key", None)
        symbol = data.get("symbol", "")
        timeframe = data.get("timeframe", "")
        indicators = data.get("indicators",
                              [])  # Lista de indicadores, ex.: [{"type": "MA", "period": 9, "value": X}, ...]
        if broker_key and symbol and timeframe:
            if broker_key not in self.monitored_pairs:
                self.monitored_pairs[broker_key] = {}
            if symbol not in self.monitored_pairs[broker_key]:
                self.monitored_pairs[broker_key][symbol] = {}
            if timeframe not in self.monitored_pairs[broker_key][symbol]:
                self.monitored_pairs[broker_key][symbol][timeframe] = {"timeframes": set(), "indicators": []}
            self.monitored_pairs[broker_key][symbol][timeframe]["timeframes"].add(timeframe)
            existing_indicators = {(ind["type"], ind["period"]) for ind in
                                   self.monitored_pairs[broker_key][symbol][timeframe]["indicators"]}
            for ind in indicators:
                if (ind.get("type"), ind.get("period")) not in existing_indicators:
                    self.monitored_pairs[broker_key][symbol][timeframe]["indicators"].append(ind)
            logger.debug(
                f"Adicionado {symbol} {timeframe} com indicadores {self.monitored_pairs[broker_key][symbol][timeframe]['indicators']} para {broker_key}")
            self._update_symbol_combo()
        else:
            logger.error(f"Dados inválidos para atualizar pares monitorados: {data}")
            self.error_occurred.emit("Erro: Dados inválidos para pares monitorados")

    def showEvent(self, event):
        if event.type() == QEvent.Type.Show:
            logger.debug("Aba Gráficos exibida, aguardando seleção de ativo/timeframe")
        super().showEvent(event)

    def _populate_combos(self):
        self.symbol_combo.clear()
        self.timeframe_combo.clear()
        self.symbol_combo.addItem("Selecione um ativo")
        self.timeframe_combo.addItem("Selecione um timeframe")
        logger.debug("Combos populados com valores iniciais")

    def _update_symbol_combo(self):
        current_broker = self.broker_combo.currentText()
        current_symbol = self.symbol_combo.currentText()
        current_timeframe = self.timeframe_combo.currentText()
        self.symbol_combo.blockSignals(True)
        self.symbol_combo.clear()
        self.symbol_combo.addItem("Selecione um ativo")
        if current_broker and current_broker in self.monitored_pairs:
            symbols = sorted(list(self.monitored_pairs[current_broker].keys()))
            for symbol in symbols:
                self.symbol_combo.addItem(symbol)
            logger.debug(f"symbol_combo atualizado para corretora {current_broker}: {symbols}")
        if current_symbol in [self.symbol_combo.itemText(i) for i in range(self.symbol_combo.count())]:
            self.symbol_combo.setCurrentText(current_symbol)
        self.symbol_combo.blockSignals(False)
        if current_broker not in self.first_stream:
            self.first_stream[current_broker] = True
        self._update_timeframe_combo(current_timeframe)

    def _update_timeframe_combo(self, preserve_timeframe=None):
        current_broker = self.broker_combo.currentText()
        current_symbol = self.symbol_combo.currentText()
        current_timeframe = preserve_timeframe or self.timeframe_combo.currentText()
        self.timeframe_combo.blockSignals(True)
        self.timeframe_combo.clear()
        self.timeframe_combo.addItem("Selecione um timeframe")
        if (current_broker and current_symbol != "Selecione um ativo" and
                current_broker in self.monitored_pairs and current_symbol in self.monitored_pairs[current_broker]):
            timeframes = sorted(list(self.monitored_pairs[current_broker][current_symbol].keys()))
            for timeframe in timeframes:
                self.timeframe_combo.addItem(timeframe)
            logger.debug(f"timeframe_combo atualizado para {current_symbol} em {current_broker}: {timeframes}")
        if current_timeframe in [self.timeframe_combo.itemText(i) for i in range(self.timeframe_combo.count())]:
            self.timeframe_combo.setCurrentText(current_timeframe)
        self.timeframe_combo.blockSignals(False)

    def _clear_chart(self):
        logger.debug(f"Limpando gráfico, itens no layout antes: {self.chart_layout.count()}")
        while self.chart_layout.count():
            item = self.chart_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
        if hasattr(self, 'figure') and self.figure:
            plt.close(self.figure)
            self.figure = None
        if hasattr(self, 'canvas') and self.canvas:
            self.canvas.setParent(None)
            self.canvas = None
        logger.debug(f"Itens no layout após limpeza: {self.chart_layout.count()}")
        if self.chart_layout.count() > 0:
            logger.error("Itens residuais no layout após limpeza, possível causa de sobreposição")
            self.error_occurred.emit("Erro: Itens residuais no layout do gráfico após limpeza")

    def _clear_buffer(self, broker_key, symbol, timeframe):
        if broker_key in self.timers and symbol in self.timers[broker_key] and timeframe in self.timers[broker_key][
            symbol]:
            self.timers[broker_key][symbol][timeframe].stop()
            self.timers[broker_key][symbol][timeframe].deleteLater()
            del self.timers[broker_key][symbol][timeframe]
            if not self.timers[broker_key][symbol]:
                del self.timers[broker_key][symbol]
            if not self.timers[broker_key]:
                del self.timers[broker_key]
            logger.debug(f"Temporizador removido para {symbol} {timeframe} na corretora {broker_key}")
        if (broker_key in self.buffer and symbol in self.buffer[broker_key] and
                timeframe in self.buffer[broker_key][symbol]):
            self.buffer[broker_key][symbol][timeframe] = []
            logger.debug(f"Buffer limpo para {symbol} {timeframe} na corretora {broker_key}")
        if (broker_key in self.chart_data and symbol in self.chart_data[broker_key] and
                timeframe in self.chart_data[broker_key][symbol]):
            self.chart_data[broker_key][symbol][timeframe] = pd.DataFrame()
            logger.debug(f"chart_data limpo para {symbol} {timeframe} na corretora {broker_key}")
        data_key = f"{broker_key}_{symbol}_{timeframe}"
        if data_key in self._last_data_length:
            del self._last_data_length[data_key]
        if data_key in self._last_candle_hash:
            del self._last_candle_hash[data_key]

    def _generate_candle_hash(self, candle_dict):
        """Gera hash seguro para um candle"""
        try:
            return f"{candle_dict.get('Open', 0)}_{candle_dict.get('High', 0)}_{candle_dict.get('Low', 0)}_{candle_dict.get('Close', 0)}_{candle_dict.get('Volume', 0)}"
        except Exception as e:
            logger.warning(f"Erro ao gerar hash do candle: {e}")
            return ""

    def _on_symbol_timeframe_changed(self):
        self.broker_combo.blockSignals(True)
        broker_key = self.broker_combo.currentText()
        symbol = self.symbol_combo.currentText()
        timeframe = self.timeframe_combo.currentText()
        n_candles = self.candles_combo.currentText()
        style = self.style_combo.currentText()
        chart_type = self.type_combo.currentText()
        logger.debug(
            f"_on_symbol_timeframe_changed: {broker_key}, {symbol}, {timeframe}, n_candles={n_candles}, style={style}, type={chart_type}")
        current_chart_key = f"{broker_key}_{symbol}_{timeframe}_{n_candles}_{style}_{chart_type}"

        if broker_key in self.timers:
            for old_symbol in list(self.timers[broker_key].keys()):
                for old_timeframe in list(self.timers[broker_key][old_symbol].keys()):
                    self.timers[broker_key][old_symbol][old_timeframe].stop()
                    self.timers[broker_key][old_symbol][old_timeframe].deleteLater()
                    del self.timers[broker_key][old_symbol][old_timeframe]
                if not self.timers[broker_key][old_symbol]:
                    del self.timers[broker_key][old_symbol]
            if not self.timers[broker_key]:
                del self.timers[broker_key]
            logger.debug(f"Todos os temporizadores removidos para corretora {broker_key}")

        if self._last_chart_key != current_chart_key:
            self._clear_chart()
            self._last_chart_key = current_chart_key
            logger.debug(
                f"Mudança detectada para {symbol} {timeframe} com {n_candles} candles, estilo {style}, tipo {chart_type} na corretora {broker_key}")

        if (symbol != "Selecione um ativo" and timeframe != "Selecione um timeframe" and
                symbol and timeframe and broker_key and
                broker_key in self.monitored_pairs and symbol in self.monitored_pairs[broker_key] and
                timeframe in self.monitored_pairs[broker_key][symbol]):
            if not (broker_key in self.historical_loaded and
                    symbol in self.historical_loaded[broker_key] and
                    timeframe in self.historical_loaded[broker_key][symbol] and
                    self.historical_loaded[broker_key][symbol][timeframe]):
                self.load_historical_data()
                logger.debug(f"Solicitando dados históricos para {symbol} {timeframe} na corretora {broker_key}")
            else:
                if (broker_key in self.chart_data and symbol in self.chart_data[broker_key] and
                        timeframe in self.chart_data[broker_key][symbol] and not self.chart_data[broker_key][symbol][
                            timeframe].empty):
                    self.update_chart()
                else:
                    logger.debug(f"Aguardando dados históricos para {symbol} {timeframe} antes de plotar")
            self._start_update_timer(broker_key, symbol, timeframe)
            logger.debug(f"Processando mudança para {symbol} {timeframe} na corretora {broker_key}")
        self.broker_combo.blockSignals(False)

    def _start_update_timer(self, broker_key, symbol, timeframe):
        """Inicia temporizador fallback com intervalos ajustados"""
        time_multipliers = {
            "PERIOD_M1": 70, "PERIOD_M5": 310, "PERIOD_M15": 910, "PERIOD_M30": 1810,
            "PERIOD_H1": 3610, "PERIOD_H4": 14410, "PERIOD_D1": 86410, "PERIOD_W1": 604810, "PERIOD_MN1": 2592010
        }
        interval = time_multipliers.get(timeframe, 3610) * 1000
        timer = QTimer(self)
        timer.timeout.connect(lambda: self.update_chart())
        timer.start(interval)
        if broker_key not in self.timers:
            self.timers[broker_key] = {}
        if symbol not in self.timers[broker_key]:
            self.timers[broker_key][symbol] = {}
        self.timers[broker_key][symbol][timeframe] = timer
        logger.debug(
            f"Temporizador fallback iniciado para {symbol} {timeframe} na corretora {broker_key} com intervalo de {interval / 1000} segundos")

    def load_historical_data(self):
        broker_key = self.broker_combo.currentText()
        symbol = self.symbol_combo.currentText()
        timeframe = self.timeframe_combo.currentText()
        if not all([broker_key, symbol != "Selecione um ativo", timeframe != "Selecione um timeframe"]):
            logger.error("Faltam corretora, ativo ou timeframe para HISTORY_DATA")
            self.error_occurred.emit("Erro: Faltam corretora, ativo ou timeframe para dados históricos")
            return
        try:
            end_time = self.broker_time_offsets.get(broker_key, int(time.time()))
            if (broker_key in self.buffer and symbol in self.buffer[broker_key] and
                    timeframe in self.buffer[broker_key][symbol] and self.buffer[broker_key][symbol][timeframe]):
                end_time = self.buffer[broker_key][symbol][timeframe][0].get("timestamp_mql", end_time)
                if not isinstance(end_time, (int, float)) or end_time <= 0:
                    logger.warning(f"timestamp_mql inválido ({end_time}) para {symbol} {timeframe}, usando tempo atual")
                    end_time = int(time.time())
                logger.debug(
                    f"Usando timestamp_mql do último candle como end_time: {datetime.utcfromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S')} UTC")

            time_multipliers = {
                "PERIOD_M1": 60, "PERIOD_M5": 300, "PERIOD_M15": 900, "PERIOD_M30": 1800,
                "PERIOD_H1": 3600, "PERIOD_H4": 14400, "PERIOD_D1": 86400, "PERIOD_W1": 604800, "PERIOD_MN1": 2592000
            }
            tf_mapping = {
                "PERIOD_M1": "M1", "PERIOD_M5": "M5", "PERIOD_M15": "M15", "PERIOD_M30": "M30",
                "PERIOD_H1": "H1", "PERIOD_H4": "H4", "PERIOD_D1": "D1", "PERIOD_W1": "W1", "PERIOD_MN1": "MN1"
            }
            seconds_per_candle = time_multipliers.get(timeframe, 3600)
            payload_timeframe = tf_mapping.get(timeframe, timeframe)
            start_time = end_time - (100 * seconds_per_candle)
            if start_time < 0:
                start_time = 0

            indicators = []
            if (broker_key in self.monitored_pairs and symbol in self.monitored_pairs[broker_key] and
                    timeframe in self.monitored_pairs[broker_key][symbol]):
                indicators = self.monitored_pairs[broker_key][symbol][timeframe]["indicators"]
            payload = {
                "symbol": symbol,
                "timeframe": payload_timeframe,
                "start_time": start_time,
                "end_time": end_time,
                "broker_key": broker_key,
                "indicators": indicators
            }
            logger.debug(
                f"Solicitando HISTORY_DATA: start_time={datetime.utcfromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')} UTC, "
                f"end_time={datetime.utcfromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S')} UTC "
                f"(100 candles, payload_timeframe={payload_timeframe}, indicadores={indicators})")
            self.command_requested.emit("HISTORY_DATA", payload)
            if broker_key not in self.historical_requested:
                self.historical_requested[broker_key] = {}
            if symbol not in self.historical_requested[broker_key]:
                self.historical_requested[broker_key][symbol] = {}
            self.historical_requested[broker_key][symbol][timeframe] = True
            if broker_key not in self.historical_loaded:
                self.historical_loaded[broker_key] = {}
            if symbol not in self.historical_loaded[broker_key]:
                self.historical_loaded[broker_key][symbol] = {}
            self.historical_loaded[broker_key][symbol][timeframe] = False
        except Exception as e:
            logger.error(f"Erro ao enviar HISTORY_DATA: {str(e)}")
            self.error_occurred.emit(f"Erro ao solicitar histórico: {str(e)}")
            if broker_key in self.historical_requested and symbol in self.historical_requested[broker_key]:
                self.historical_requested[broker_key][symbol][timeframe] = False

    @Slot(dict)
    def process_historical_data(self, data):
        broker_key = data.get("broker_key", "N/A")
        symbol = data.get("payload", {}).get("symbol", self.symbol_combo.currentText())
        timeframe = data.get("payload", {}).get("timeframe", self.timeframe_combo.currentText())
        candles = data.get("data", []) or data.get("rates", [])
        if not all([symbol, timeframe, candles]):
            logger.error(f"Dados históricos inválidos recebidos: {data}")
            self.error_occurred.emit("Erro: Dados históricos inválidos recebidos")
            if broker_key in self.historical_requested and symbol in self.historical_requested[broker_key]:
                self.historical_requested[broker_key][symbol][timeframe] = False
            return
        if len(candles) < 1:
            logger.error(f"Nenhum candle recebido para {symbol} {timeframe}")
            self.error_occurred.emit("Erro: Nenhum dado histórico recebido")
            if broker_key in self.historical_requested and symbol in self.historical_requested[broker_key]:
                self.historical_requested[broker_key][symbol][timeframe] = False
            return
        if len(candles) < 100:
            logger.warning(f"Recebidos apenas {len(candles)} candles para {symbol} {timeframe}, esperado até 100")

        time_multipliers = {
            "PERIOD_M1": 60, "PERIOD_M5": 300, "PERIOD_M15": 900, "PERIOD_M30": 1800,
            "PERIOD_H1": 3600, "PERIOD_H4": 14400, "PERIOD_D1": 86400, "PERIOD_W1": 604800, "PERIOD_MN1": 2592000
        }
        seconds_per_candle = time_multipliers.get(timeframe, 3600)
        current_time = self.broker_time_offsets.get(broker_key, int(time.time()))
        filtered_candles = [
            candle for candle in candles
            if candle.get("time", 0) < (current_time - seconds_per_candle)
        ]
        if len(candles) > len(filtered_candles):
            logger.debug(
                f"Filtrado {len(candles) - len(filtered_candles)} candle(s) em formação para {symbol} {timeframe}")

        normalized_candles = []
        for candle in filtered_candles:
            normalized_candle = candle.copy()
            if '' in candle and isinstance(candle[''], list):
                normalized_candle['indicators'] = candle['']
                del normalized_candle['']
                logger.debug(f"Normalizado indicadores de chave '' para 'indicators' em candle: {normalized_candle}")
            normalized_candles.append(normalized_candle)

        candle_times = [datetime.fromtimestamp(candle.get("time", 0)).strftime('%Y-%m-%d %H:%M:%S') for candle in
                        normalized_candles]
        logger.debug(
            f"Candles recebidos para {symbol} {timeframe}: {len(normalized_candles)} candles, timestamps: {candle_times}")

        if broker_key not in self.buffer:
            self.buffer[broker_key] = {}
        if symbol not in self.buffer[broker_key]:
            self.buffer[broker_key][symbol] = {}
        if timeframe not in self.buffer[broker_key][symbol]:
            self.buffer[broker_key][symbol][timeframe] = []

        existing_times = {candle.get("time", 0) for candle in self.buffer[broker_key][symbol][timeframe]}
        unique_candles = [candle for candle in normalized_candles if candle.get("time", 0) not in existing_times]
        self.buffer[broker_key][symbol][timeframe].extend(unique_candles)
        self.buffer[broker_key][symbol][timeframe] = sorted(self.buffer[broker_key][symbol][timeframe],
                                                            key=lambda x: x.get("time", 0), reverse=True)[:100]
        logger.debug(
            f"Buffer atualizado com histórico para {symbol} {timeframe}: {len(self.buffer[broker_key][symbol][timeframe])} candles")

        historical_rows = []
        indicator_keys = set()
        for candle in self.buffer[broker_key][symbol][timeframe]:
            row = {
                "Date": pd.to_datetime(candle.get("time", 0), unit="s"),
                "Open": candle.get("open", 0),
                "High": candle.get("high", 0),
                "Low": candle.get("low", 0),
                "Close": candle.get("close", 0),
                "Volume": candle.get("volume", 0)
            }
            if "indicators" in candle and isinstance(candle["indicators"], list):
                for ind in candle["indicators"]:
                    try:
                        if isinstance(ind, dict) and "type" in ind and "period" in ind and "value" in ind:
                            ind_key = f"{ind['type']}_{ind['period']}"
                            row[ind_key] = ind["value"]
                            indicator_keys.add(ind_key)
                        else:
                            logger.warning(f"Indicador malformado em {symbol} {timeframe}: {ind}")
                    except (KeyError, TypeError) as e:
                        logger.warning(f"Erro ao processar indicador {ind} para {symbol} {timeframe}: {e}")
            historical_rows.append(row)
        df = pd.DataFrame(historical_rows).sort_values("Date")
        if broker_key not in self.chart_data:
            self.chart_data[broker_key] = {}
        if symbol not in self.chart_data[broker_key]:
            self.chart_data[broker_key][symbol] = {}
        self.chart_data[broker_key][symbol][timeframe] = df.tail(100)
        logger.debug(
            f"Dados históricos processados para {symbol} {timeframe}: {len(df)} candles, colunas: {df.columns.tolist()}, indicadores: {indicator_keys}")
        self.historical_loaded[broker_key][symbol][timeframe] = True
        self.historical_requested[broker_key][symbol][timeframe] = False

        if (broker_key == self.broker_combo.currentText() and
                symbol == self.symbol_combo.currentText() and
                timeframe == self.timeframe_combo.currentText()):
            logger.debug(f"Forçando atualização do gráfico para {symbol} {timeframe} após dados históricos")
            self._last_chart_key = None
            self.update_chart()

    @Slot(dict)
    def update_stream_data(self, data):
        if self.paused:
            logger.debug("Atualização de stream pausada")
            return
        broker_key = data.get("broker_key", "N/A")
        symbol = data.get("symbol", None)
        timeframe = data.get("timeframe", None)
        ohlc = data.get("ohlc", {})
        timestamp_mql = data.get("timestamp_mql", 0)
        indicators = data.get("indicators", [])
        if not all([symbol, timeframe, ohlc]) or not isinstance(ohlc.get("time", 0), (int, float)) or ohlc.get("time",
                                                                                                               0) <= 0:
            logger.error(f"Dados de stream inválidos recebidos: {data}")
            self.error_occurred.emit("Erro: Dados de stream inválidos recebidos")
            return

        if broker_key not in self.buffer:
            self.buffer[broker_key] = {}
        if symbol not in self.buffer[broker_key]:
            self.buffer[broker_key][symbol] = {}
        if timeframe not in self.buffer[broker_key][symbol]:
            self.buffer[broker_key][symbol][timeframe] = []

        new_candle = {
            "time": ohlc.get("time", 0),
            "open": ohlc.get("open", 0),
            "high": ohlc.get("high", 0),
            "low": ohlc.get("low", 0),
            "close": ohlc.get("close", 0),
            "volume": ohlc.get("volume", 0),
            "timestamp_mql": ohlc.get("time", timestamp_mql),
            "indicators": indicators
        }
        buffer_list = self.buffer[broker_key][symbol][timeframe]
        if not buffer_list or new_candle["time"] > buffer_list[0]["time"]:
            buffer_list.append(new_candle)
            self.buffer[broker_key][symbol][timeframe] = sorted(buffer_list, key=lambda x: x.get("time", 0),
                                                                reverse=True)[:100]
            logger.debug(f"Novo candle de stream adicionado para {symbol} {timeframe}: {new_candle}")

            historical_rows = []
            indicator_keys = set()
            for candle in buffer_list:
                row = {
                    "Date": pd.to_datetime(candle["time"], unit="s"),
                    "Open": candle["open"],
                    "High": candle["high"],
                    "Low": candle["low"],
                    "Close": candle["close"],
                    "Volume": candle["volume"]
                }
                for ind in candle.get("indicators", []):
                    try:
                        if isinstance(ind, dict) and "type" in ind and "period" in ind and "value" in ind:
                            ind_key = f"{ind['type']}_{ind['period']}"
                            row[ind_key] = ind["value"]
                            indicator_keys.add(ind_key)
                        else:
                            logger.warning(f"Indicador malformado em stream para {symbol} {timeframe}: {ind}")
                    except (KeyError, TypeError) as e:
                        logger.warning(f"Erro ao processar indicador {ind} para {symbol} {timeframe}: {e}")
                historical_rows.append(row)
            df = pd.DataFrame(historical_rows).sort_values("Date")
            if broker_key not in self.chart_data:
                self.chart_data[broker_key] = {}
            if symbol not in self.chart_data[broker_key]:
                self.chart_data[broker_key][symbol] = {}
            self.chart_data[broker_key][symbol][timeframe] = df.tail(100)

            data_key = f"{broker_key}_{symbol}_{timeframe}"
            self._last_data_length[data_key] = len(df)
            if not df.empty:
                last_candle = df.tail(1).to_dict('records')[0]
                self._last_candle_hash[data_key] = self._generate_candle_hash(last_candle)

            if (broker_key == self.broker_combo.currentText() and
                    symbol == self.symbol_combo.currentText() and
                    timeframe == self.timeframe_combo.currentText()):
                if (broker_key in self.historical_loaded and
                        symbol in self.historical_loaded[broker_key] and
                        timeframe in self.historical_loaded[broker_key][symbol] and
                        self.historical_loaded[broker_key][symbol][timeframe]):
                    logger.debug(f"Atualizando gráfico IMEDIATAMENTE para {symbol} {timeframe} com novo candle")
                    self.update_chart()
                else:
                    logger.debug(
                        f"Aguardando dados históricos para {symbol} {timeframe} antes de atualizar gráfico com stream")
            else:
                logger.debug(f"Candle de stream ignorado: {symbol} {timeframe} não é o par ativo")
        else:
            logger.debug(f"Candle de stream ignorado para {symbol} {timeframe}: timestamp duplicado ou mais antigo")

        logger.debug(f"Estado do buffer após stream: {len(self.buffer[broker_key][symbol][timeframe])} candles")

    def update_chart(self):
        if self._is_plotting:
            logger.debug("Plotagem em andamento, ignorando chamada de update_chart")
            return
        self._is_plotting = True
        plotting_timer = QTimer()
        plotting_timer.setSingleShot(True)
        plotting_timer.timeout.connect(lambda: logger.error("Timeout ao plotar gráfico") or self.error_occurred.emit(
            "Erro: Timeout ao plotar gráfico"))
        plotting_timer.start(5000)
        try:
            broker_key = self.broker_combo.currentText()
            symbol = self.symbol_combo.currentText()
            timeframe = self.timeframe_combo.currentText()
            n_candles = self.candles_combo.currentText()
            style = self.style_combo.currentText()
            chart_type = self.type_combo.currentText()
            subwindow = self.rsi_volume_combo.currentText()
            current_chart_key = f"{broker_key}_{symbol}_{timeframe}_{n_candles}_{style}_{chart_type}_{subwindow}"
            data_key = f"{broker_key}_{symbol}_{timeframe}"

            if not all([broker_key, symbol,
                        timeframe]) or symbol == "Selecione um ativo" or timeframe == "Selecione um timeframe":
                logger.debug("Não há corretora, ativo ou timeframe selecionados para plotar gráfico")
                self._clear_chart()
                return
            if not (broker_key in self.chart_data and symbol in self.chart_data[broker_key] and
                    timeframe in self.chart_data[broker_key][symbol]):
                logger.debug(f"Aguardando dados para {symbol} {timeframe} na corretora {broker_key}")
                return
            df = self.chart_data[broker_key][symbol][timeframe]
            if df.empty:
                logger.warning(f"Dados vazios para {symbol} {timeframe} na corretora {broker_key}")
                self.error_occurred.emit(f"Erro: Dados vazios para {symbol} {timeframe}")
                return

            n_candles = min(int(self.candles_combo.currentText()), len(df))
            df_plot = df.tail(n_candles).copy()

            current_data_length = len(df)
            last_data_length = self._last_data_length.get(data_key, 0)
            current_candle_hash = ""
            if not df_plot.empty:
                last_candle = df_plot.tail(1).to_dict('records')[0]
                current_candle_hash = self._generate_candle_hash(last_candle)
            last_candle_hash = self._last_candle_hash.get(data_key, "")
            last_indicator_columns = self._last_data_length.get(f"{data_key}_indicators", [])
            indicator_columns = [col for col in df_plot.columns if
                                 col not in ["Date", "Open", "High", "Low", "Close", "Volume"]]
            styles_changed = self.indicator_styles != self._last_indicator_styles

            needs_replot = (
                    self._last_chart_key != current_chart_key or
                    self.canvas is None or
                    current_data_length != last_data_length or
                    current_candle_hash != last_candle_hash or
                    sorted(indicator_columns) != sorted(last_indicator_columns) or
                    styles_changed
            )

            if not needs_replot:
                logger.debug(f"Gráfico já atualizado para {symbol} {timeframe}, sem mudanças detectadas")
                return

            datetime_format = (
                "%H:%M" if timeframe in ["PERIOD_M1", "PERIOD_M5", "PERIOD_M15", "PERIOD_M30"] else
                "%d %H:%M" if timeframe in ["PERIOD_H1", "PERIOD_H4"] else
                "%b %d"
            )

            is_historical = (broker_key in self.historical_loaded and
                             symbol in self.historical_loaded[broker_key] and
                             timeframe in self.historical_loaded[broker_key][symbol] and
                             self.historical_loaded[broker_key][symbol][timeframe])
            data_source = "históricos" if is_historical else "stream"

            trigger = "combo_change"
            if current_data_length > last_data_length:
                trigger = "new_data"
            elif current_candle_hash != last_candle_hash:
                trigger = "candle_update"
            elif self._last_chart_key != current_chart_key:
                trigger = "config_change"
            elif sorted(indicator_columns) != sorted(last_indicator_columns):
                trigger = "new_indicators"
            elif styles_changed:
                trigger = "style_change"

            # Preparar indicadores não-RSI para a janela principal com legenda
            add_plots = []
            for col in indicator_columns:
                if col.startswith("RSI_"):
                    continue  # Ignorar RSI para a janela principal
                if col in df_plot and not df_plot[col].isna().all():
                    indicator_style = self.indicator_styles.get(col,
                                                                {"color": "#FF0000", "linestyle": "-", "width": 0.8})
                    add_plots.append(mpf.make_addplot(
                        df_plot[col],
                        type='line',
                        linestyle=indicator_style["linestyle"],
                        width=indicator_style["width"],
                        color=indicator_style["color"],
                        ylabel=col,
                        label=col,
                        panel=0
                    ))
                else:
                    logger.warning(f"Coluna de indicador {col} ignorada: contém apenas NaN")

            # Configurar RSI apenas na subjanela (panel=1) quando selecionado
            rsi_plot = None
            if subwindow == "RSI":
                rsi_columns = [col for col in indicator_columns if col.startswith("RSI_")]
                if rsi_columns:
                    rsi_col = rsi_columns[0]
                    if rsi_col in df_plot and not df_plot[rsi_col].isna().all():
                        indicator_style = self.indicator_styles.get(rsi_col,
                                                                    {"color": "#FF0000", "linestyle": "-", "width": 0.8})
                        rsi_plot = mpf.make_addplot(
                            df_plot[rsi_col],
                            panel=1,
                            type='line',
                            linestyle=indicator_style["linestyle"],
                            width=indicator_style["width"],
                            color=indicator_style["color"],
                            ylabel=rsi_col
                        )
                    else:
                        logger.warning(f"Coluna RSI {rsi_col} ignorada: contém apenas NaN")
                else:
                    logger.warning("Nenhum indicador RSI disponível para plotar")

            logger.debug(f"Plotando gráfico para {symbol} {timeframe} com {n_candles} candles, "
                         f"len(df)={len(df)}, fonte: {data_source}, trigger: {trigger}, "
                         f"estilo: {style}, tipo: {chart_type}, datetime_format: {datetime_format}, "
                         f"indicadores: {indicator_columns}, subjanela: {subwindow}")

            self._clear_chart()
            plot_list = add_plots if add_plots else []
            if rsi_plot:
                plot_list.append(rsi_plot)
            # ALTERAÇÃO: Passar addplot apenas se plot_list não estiver vazia
            kwargs = {
                "type": chart_type,
                "style": style,
                "volume": (subwindow == "Volume"),
                "datetime_format": datetime_format,
                "returnfig": True
            }
            if plot_list:
                kwargs["addplot"] = plot_list
            fig, axlist = mpf.plot(df_plot.set_index("Date"), **kwargs)
            self.figure = fig
            self.canvas = FigureCanvas(self.figure)
            self.chart_layout.addWidget(self.canvas)
            self.canvas.draw()

            self._last_chart_key = current_chart_key
            self._last_data_length[data_key] = current_data_length
            self._last_data_length[f"{data_key}_indicators"] = indicator_columns
            self._last_candle_hash[data_key] = current_candle_hash
            self._last_indicator_styles = self.indicator_styles.copy()

            logger.debug(f"Gráfico renderizado para {symbol} {timeframe} com {n_candles} candles, "
                         f"estilo {style}, tipo: {chart_type}, indicadores: {indicator_columns}, subjanela: {subwindow}")
        except Exception as e:
            logger.error(f"Erro ao plotar gráfico: {str(e)}")
            self.error_occurred.emit(f"Erro ao plotar gráfico: {str(e)}")
        finally:
            plotting_timer.stop()
            self._is_plotting = False