# Arquivo: gui/widgets/boleta_history_trades_tab.py
# Versão: 1.0.9.o - Envio 3 (Correção: Remove requisição inicial de dados da aba de histórico)

import logging
import time
from datetime import datetime, timedelta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QGroupBox, QGridLayout, QLabel,
    QDateEdit, QLineEdit, QComboBox, QPushButton, QMessageBox
)
from PySide6.QtCore import Slot, Qt, QDate

logger = logging.getLogger(__name__)


class BoletaHistoryTradesTab(QWidget):
    def __init__(self, broker_key, zmq_message_handler, request_new_history_from_ea_callback, parent=None):
        super().__init__(parent)
        self.broker_key = broker_key
        self.zmq_message_handler = zmq_message_handler
        self.request_new_history_from_ea_callback = request_new_history_from_ea_callback  # Callback para solicitar novos dados ao EA

        self._all_loaded_history_data = []  # Armazena todos os dados brutos recebidos do EA
        self._loaded_start_ts = 0  # Timestamp Unix do início do período dos dados carregados do EA
        self._loaded_end_ts = 0  # Timestamp Unix do fim do período dos dados carregados do EA

        self.setup_ui()
        self._connect_signals()
        logger.debug(f"[HistoryTab] BoletaHistoryTradesTab inicializada para {self.broker_key}.")

        # REMOVIDO: A chamada inicial de _set_initial_filter_dates_and_fetch()
        # A requisição inicial de dados será agora controlada pelo BoletaTraderGui
        # para evitar requisições duplicadas.
        # self._set_initial_filter_dates_and_fetch() # <--- REMOVIDO OU COMENTADO ESTA LINHA


    # ... (Restante do método setup_ui permanece inalterado) ...
    def setup_ui(self):
        layout = QVBoxLayout(self)

        # --- Grupo de Filtros ---
        filter_group_box = QGroupBox("Filtros de Histórico")
        filter_layout = QGridLayout()

        # Data Início e Data Fim
        filter_layout.addWidget(QLabel("Data Início:"), 0, 0)
        self.start_date_edit = QDateEdit(QDate.currentDate())
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("dd/MM/yyyy")
        filter_layout.addWidget(self.start_date_edit, 0, 1)

        filter_layout.addWidget(QLabel("Data Fim:"), 0, 2)
        self.end_date_edit = QDateEdit(QDate.currentDate())
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("dd/MM/yyyy")
        filter_layout.addWidget(self.end_date_edit, 0, 3)

        # Símbolo
        filter_layout.addWidget(QLabel("Símbolo:"), 1, 0)
        self.symbol_filter_lineedit = QLineEdit()
        filter_layout.addWidget(self.symbol_filter_lineedit, 1, 1)

        # Tipo de Operação (BUY/SELL)
        filter_layout.addWidget(QLabel("Tipo:"), 1, 2)
        self.type_filter_combobox = QComboBox()
        self.type_filter_combobox.addItems(["Todos", "BUY", "SELL"])
        filter_layout.addWidget(self.type_filter_combobox, 1, 3)

        # Resultado (Lucro/Prejuízo)
        filter_layout.addWidget(QLabel("Resultado:"), 2, 0)
        self.result_filter_combobox = QComboBox()
        self.result_filter_combobox.addItems(["Todos", "Lucro", "Prejuízo"])
        filter_layout.addWidget(self.result_filter_combobox, 2, 1)

        # Comentário
        filter_layout.addWidget(QLabel("Comentário:"), 2, 2)
        self.comment_filter_lineedit = QLineEdit()
        filter_layout.addWidget(self.comment_filter_lineedit, 2, 3)

        # Botão Aplicar Filtros
        self.apply_filters_button = QPushButton("Aplicar Filtros")
        filter_layout.addWidget(self.apply_filters_button, 3, 0, 1, 4)  # Span across 4 columns

        filter_group_box.setLayout(filter_layout)
        layout.addWidget(filter_group_box)
        # --- Fim Grupo de Filtros ---

        self.table = QTableWidget()
        # Colunas ajustadas: REMOVIDAS SL e TP
        self.table.setColumnCount(10)  # Reduzido para 10 colunas
        self.table.setHorizontalHeaderLabels(
            ["Ticket", "Símbolo", "Tipo", "Volume", "Preço Abertura", "Preço Fechamento",
             "Lucro", "Tempo Abertura", "Tempo Fechamento", "Comentário"])

        # Ajuste as larguras das colunas conforme necessário
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 120)
        self.table.setColumnWidth(5, 120)
        self.table.setColumnWidth(6, 100)
        self.table.setColumnWidth(7, 150)
        self.table.setColumnWidth(8, 150)
        self.table.setColumnWidth(9, 150)

        self.table.setMinimumHeight(400)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                alternate-background-color: #f0f0f0;
            }
        """)
        self.table.setObjectName(f"history_{self.broker_key}")
        layout.addWidget(self.table)
        self.setLayout(layout)

    def _connect_signals(self):
        self.zmq_message_handler.history_trades_received.connect(self.update_data)

        # Conectar os sinais dos filtros de texto/combobox para APENAS filtrar localmente
        self.symbol_filter_lineedit.textChanged.connect(self._apply_local_filters)
        self.type_filter_combobox.currentIndexChanged.connect(self._apply_local_filters)
        self.result_filter_combobox.currentIndexChanged.connect(self._apply_local_filters)
        self.comment_filter_lineedit.textChanged.connect(self._apply_local_filters)

        # Conectar os sinais de data e o botão 'Aplicar Filtros' para a NOVA LÓGICA de verificação e fetch ao EA
        self.start_date_edit.dateChanged.connect(self._on_date_or_button_filter_changed)
        self.end_date_edit.dateChanged.connect(self._on_date_or_button_filter_changed)
        self.apply_filters_button.clicked.connect(self._on_date_or_button_filter_changed)

    def _set_initial_filter_dates_and_fetch(self):
        """Define as datas iniciais dos filtros (Data Fim = hoje, Data Início = 7 dias atrás)
        e PREPARA o filtro inicial, mas NÃO dispara o fetch imediato ao EA.
        A requisição ao EA será feita pelo BoletaTraderGui._on_broker_status_updated.
        """
        end_date = QDate.currentDate()
        start_date = end_date.addDays(-7)  # 7 dias atrás

        # Bloquear sinais para não disparar _on_date_or_button_filter_changed durante a inicialização
        self.start_date_edit.blockSignals(True)
        self.end_date_edit.blockSignals(True)
        self.end_date_edit.setDate(end_date)
        self.start_date_edit.setDate(start_date)
        self.start_date_edit.blockSignals(False)
        self.end_date_edit.blockSignals(False)

        # REMOVIDO: A chamada para _on_date_or_button_filter_changed() foi retirada daqui.
        # Agora, a lógica de requisição de dados ao EA é controlada EXCLUSIVAMENTE
        # pelo `BoletaTraderGui._on_broker_status_updated` quando uma corretora
        # é reconhecida como registrada. Isso elimina a requisição duplicada e a race condition.


    @Slot(dict)
    def update_data(self, history_data):
        """
        Recebe os dados brutos do histórico do EA, armazena-os e, em seguida, aplica os filtros locais.
        Esta função SÓ RECEBE dados, não solicita.
        """
        if history_data.get("broker_key") != self.broker_key:
            return

        closed_positions = history_data.get("trades", [])
        if not isinstance(closed_positions, list):
            closed_positions = []

        # Atualiza os dados brutos carregados
        self._all_loaded_history_data = closed_positions

        # Atualiza os timestamps de início/fim dos dados carregados (para controle de cache)
        if closed_positions:
            min_ts = float('inf')
            max_ts = 0
            for pos in closed_positions:
                time_open = pos.get('time_open', 0)
                time_close = pos.get('time_close', 0)
                if time_open > 0 and time_open < min_ts: min_ts = time_open
                if time_close > 0 and time_close < min_ts: min_ts = time_close
                if time_open > max_ts: max_ts = time_open
                if time_close > max_ts: max_ts = time_close
            self._loaded_start_ts = min_ts if min_ts != float('inf') else 0
            self._loaded_end_ts = max_ts
            logger.debug(
                f"[HistoryTab] Dados brutos recebidos do EA. Período carregado: {self._format_timestamp(self._loaded_start_ts)} a {self._format_timestamp(self._loaded_end_ts)}.")
        else:
            self._loaded_start_ts = 0
            self._loaded_end_ts = 0
            logger.debug("[HistoryTab] Nenhum dado bruto recebido do EA.")

        # Aplica os filtros locais após novos dados chegarem
        self._apply_local_filters()

    @Slot()
    def _on_date_or_button_filter_changed(self):
        """
        Este método é acionado por mudanças nas datas ou pelo clique no botão 'Aplicar Filtros'.
        Ele verifica se novos dados precisam ser buscados do EA e, se não, apenas aplica os filtros locais.
        """
        start_date_q = self.start_date_edit.date()
        end_date_q = self.end_date_edit.date()

        # --- Validação e Ajuste do Período de 60 dias ---
        # A Data Início não pode ser posterior à Data Fim
        if start_date_q > end_date_q:
            QMessageBox.warning(self, "Erro de Período", "A Data Início não pode ser posterior à Data Fim.")
            # Reverte a data de início para a data de fim para evitar loops e inconsistências.
            # Bloquear sinais para não disparar _on_date_or_button_filter_changed recursivamente
            self.start_date_edit.blockSignals(True)
            self.start_date_edit.setDate(end_date_q)
            self.start_date_edit.blockSignals(False)
            # Continua o fluxo para aplicar filtros locais com a data ajustada.

        diff_days = self.start_date_edit.date().daysTo(
            self.end_date_edit.date())  # Usa as datas atuais dos widgets, que podem ter sido ajustadas
        if diff_days > 60:
            adjusted_end_date_q = self.start_date_edit.date().addDays(60)

            # Bloquear sinais para evitar chamadas recursivas ao ajustar a data
            self.end_date_edit.blockSignals(True)
            self.end_date_edit.setDate(adjusted_end_date_q)
            self.end_date_edit.blockSignals(False)

            QMessageBox.warning(self, "Período de Histórico Excedido",
                                f"O período selecionado excede 60 dias. Ajustando a Data Fim para {adjusted_end_date_q.toString('dd/MM/yyyy')}.")
            logger.warning(f"[HistoryTab] Período de histórico ajustado para o máximo de 60 dias.")
            # O fluxo continua para verificar o fetch/aplicar filtros com as datas ajustadas.

        # Converter QDate para timestamp UNIX (segundos) para o período de filtro
        # Usar os valores atuais dos QDateEdit, que podem ter sido ajustados
        start_ts_filter = self.start_date_edit.date().startOfDay().toSecsSinceEpoch()
        end_ts_filter = self.end_date_edit.date().endOfDay().toSecsSinceEpoch()

        # --- Lógica para Decidir se Precisa de Novo Fetch do EA ---
        # Margem de tolerância de 1 segundo para evitar problemas de arredondamento de timestamp.
        # Se a Data de Início do filtro for anterior à Data de Início do cache carregado,
        # OU a Data de Fim do filtro for posterior à Data de Fim do cache carregado,
        # OU não temos dados brutos carregados,
        # ENTÃO precisamos de um novo fetch do EA.
        needs_ea_fetch = False
        if not self._all_loaded_history_data:  # Primeiro fetch ou dados limpos
            needs_ea_fetch = True
            logger.debug("[HistoryTab] Nenhum dado bruto carregado ou cache vazio. Solicitando ao EA.")
        elif (start_ts_filter < self._loaded_start_ts - 1 or  # Filtro começa antes do cache
              end_ts_filter > self._loaded_end_ts + 1):  # Filtro termina depois do cache
            needs_ea_fetch = True
            logger.debug("[HistoryTab] Período de filtro fora do cache atual. Solicitando ao EA.")
            logger.debug(
                f"[HistoryTab] Cache: [{self._format_timestamp(self._loaded_start_ts)} - {self._format_timestamp(self._loaded_end_ts)}]")
            logger.debug(
                f"[HistoryTab] Filtro: [{self._format_timestamp(start_ts_filter)} - {self._format_timestamp(end_ts_filter)}]")

        if needs_ea_fetch:
            logger.info(
                f"[HistoryTab] Chamando callback para solicitar dados do EA: {self.broker_key}, de {self._format_timestamp(start_ts_filter)} a {self._format_timestamp(end_ts_filter)}.")
            self.request_new_history_from_ea_callback(self.broker_key, start_ts_filter, end_ts_filter)
        else:
            logger.debug("[HistoryTab] Período de filtro dentro do cache. Aplicando filtros locais.")
            # Se não precisa de fetch, apenas aplica os filtros locais com os dados já em memória.
            self._apply_local_filters()

    def _apply_local_filters(self):
        """
        Aplica os filtros selecionados localmente nos dados que já estão carregados em self._all_loaded_history_data.
        Esta função APENAS FILTRA e ATUALIZA A TABELA, não solicita dados ao EA.
        """
        filtered_positions = []

        # Pega os valores dos filtros da UI
        start_date_q = self.start_date_edit.date()
        end_date_q = self.end_date_edit.date()
        start_ts_filter = start_date_q.startOfDay().toSecsSinceEpoch()
        end_ts_filter = end_date_q.endOfDay().toSecsSinceEpoch()

        symbol_filter_text = self.symbol_filter_lineedit.text().strip().upper()
        type_filter_text = self.type_filter_combobox.currentText()
        result_filter_text = self.result_filter_combobox.currentText()
        comment_filter_text = self.comment_filter_lineedit.text().strip()

        for pos in self._all_loaded_history_data:
            # Filtro por período (essencial para dados carregados)
            pos_time_open = pos.get('time_open', 0)
            pos_time_close = pos.get('time_close', 0)

            # Ajuste de filtro de tempo: a posição deve ter **alguma sobreposição** com o período do filtro
            # Ou o início ou o fim da posição está dentro do período do filtro,
            # OU a posição "envelopa" o período do filtro.
            if not ((pos_time_open >= start_ts_filter and pos_time_open <= end_ts_filter) or
                    (pos_time_close >= start_ts_filter and pos_time_close <= end_ts_filter) or
                    (pos_time_open <= start_ts_filter and pos_time_close >= end_ts_filter) or
                    (pos_time_open == 0 and pos_time_close == 0)):  # Caso excepcional para dados sem tempo
                continue

            # Filtro por Símbolo
            if symbol_filter_text and symbol_filter_text not in pos.get('symbol', '').upper():
                continue

            # Filtro por Tipo de Operação (BUY/SELL)
            if type_filter_text != "Todos" and pos.get('type') != type_filter_text:
                continue

            # Filtro por Resultado (Lucro/Prejuízo)
            profit = pos.get('profit', 0.0)
            if result_filter_text == "Lucro" and profit <= 0:
                continue
            if result_filter_text == "Prejuízo" and profit >= 0:
                continue

            # Filtro por Comentário
            if comment_filter_text and comment_filter_text not in pos.get('comment', ''):
                continue

            filtered_positions.append(pos)

        # Preencher a tabela com os resultados filtrados
        self.table.clearContents()
        self.table.setRowCount(len(filtered_positions))
        for row, pos in enumerate(filtered_positions):
            self._populate_history_row(row, pos)

        logger.debug(
            f"[HistoryTab] Tabela de histórico atualizada para {self.broker_key} com {len(filtered_positions)} posições filtradas.")

    def _populate_history_row(self, row, pos):
        """
        Preenche uma linha na tabela de histórico de trades.
        """
        # --- MAPEAMENTO DOS CAMPOS - REMOVIDAS SL e TP ---
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

        price_open_item = QTableWidgetItem(f"{float(pos.get('price_open', 0.0)):.5f}")
        price_open_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 4, price_open_item)

        price_close_item = QTableWidgetItem(f"{float(pos.get('price_close', 0.0)):.5f}")
        price_close_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 5, price_close_item)

        profit = pos.get('profit', 0.0)
        profit_item = QTableWidgetItem(f"{profit:.2f}")
        profit_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 6, profit_item)

        time_open_item = QTableWidgetItem(self._format_timestamp(pos.get("time_open", 0)))
        time_open_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 7, time_open_item)

        time_close_item = QTableWidgetItem(self._format_timestamp(pos.get("time_close", 0)))
        time_close_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 8, time_close_item)

        # Comentário (agora na coluna 9)
        comment_item = QTableWidgetItem(str(pos.get("comment", "")))
        comment_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 9, comment_item)

    def _format_timestamp(self, timestamp: int) -> str:
        """
        Formata um timestamp UNIX (inteiro) para uma string de data e hora legível.
        Retorna uma string vazia em caso de erro na formatação.
        """
        if timestamp <= 0:
            return ""
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            logger.error(f"[HistoryTab] Erro ao formatar timestamp {timestamp}: {str(e)}")
            return ""

# Arquivo: gui/widgets/boleta_history_trades_tab.py
# Versão: 1.0.9.o - Envio 3 (Correção: Remove requisição inicial de dados da aba de histórico)