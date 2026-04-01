# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# gui/main_window.py
# Janela principal com sidebar de navegação e header com monitor de sistema.

import logging
import asyncio
from PySide6.QtWidgets import (
    QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QLabel,
    QPushButton, QStackedWidget, QFrame, QSizePolicy, QMessageBox
)
from PySide6.QtGui import QCloseEvent, QFont, QIcon
from PySide6.QtCore import Slot, Qt, Signal, QTimer

from core.config_manager import ConfigManager
from core.broker_manager import BrokerManager
from core.zmq_router import ZmqRouter
from core.zmq_message_handler import ZmqMessageHandler
from internet_monitor import InternetMonitor
from gui import themes

from gui.pages.dashboard_page import DashboardPage
from gui.pages.brokers_page import BrokersPage
from gui.pages.history_page import HistoryPage
from gui.pages.logs_page import LogsPage
from gui.pages.settings_page import SettingsPage

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    broker_status_updated = Signal(dict, dict)
    broker_connected = Signal(str)

    def __init__(self,
                 config: ConfigManager,
                 broker_manager: BrokerManager,
                 zmq_router: ZmqRouter,
                 shutdown_event_ref: asyncio.Event,
                 root_path: str,
                 mt5_monitor,
                 copytrade_manager=None):
        super().__init__()
        self.config = config
        self.broker_manager = broker_manager
        self.zmq_router = zmq_router
        self.shutdown_event_ref = shutdown_event_ref
        self.root_path = root_path
        self.mt5_monitor = mt5_monitor
        self.copytrade_manager = copytrade_manager
        self.zmq_message_handler = ZmqMessageHandler(
            config, zmq_router, broker_manager=broker_manager,
            copytrade_manager=copytrade_manager
        )

        self.brokers = self.broker_manager.load_brokers()
        self.broker_status = {}
        self.broker_modes = {}
        for key, broker in self.brokers.items():
            self.broker_status[key] = False
            self.broker_modes[key] = broker.get("mode", "Hedge")

        # Carregar tema salvo
        saved_theme = self.config.get('GUI', 'theme', fallback='Escuro')
        themes.set_theme(saved_theme)

        self.setWindowTitle("EPCopyFlow 2.0")
        self.setGeometry(50, 50, 1200, 750)
        self.setMinimumSize(900, 550)

        self._init_ui()
        self._connect_signals()

        # Internet monitor (QTimer-based, runs in GUI thread - thread-safe)
        self.internet_monitor = InternetMonitor(check_interval=5, parent=self)
        self.internet_monitor.status_updated.connect(self._on_system_status)
        self.internet_monitor.start()

        # Timer para polling periódico dos indicadores de status (detecta MT5 fechando)
        self.indicators_timer = QTimer(self)
        self.indicators_timer.timeout.connect(self._update_all_indicators)
        self.indicators_timer.start(2000)  # 2 segundos

        logger.info("MainWindow inicializada.")

    # ── UI Setup ──
    def _init_ui(self):
        central = QWidget()
        central.setObjectName("main-area")
        central.setStyleSheet(themes.main_area_style())
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Header
        self.header = self._create_header()
        root_layout.addWidget(self.header)

        # Body = sidebar + stacked pages
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.sidebar = self._create_sidebar()
        body_layout.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.pages.setStyleSheet(themes.page_background_style())

        # Create pages
        self.dashboard_page = DashboardPage(
            self.broker_manager, self.copytrade_manager,
            zmq_message_handler=self.zmq_message_handler
        )
        self.dashboard_page.set_broker_status(self.broker_status)
        self.brokers_page = BrokersPage(
            self.config, self.broker_manager, self.zmq_router, self.mt5_monitor,
            zmq_message_handler=self.zmq_message_handler
        )
        self.brokers_page.set_broker_status(self.broker_status)
        self.history_page = HistoryPage(self.copytrade_manager)
        self.logs_page = LogsPage()
        self.settings_page = SettingsPage(self.config, on_theme_changed=self.apply_theme)

        self.pages.addWidget(self.dashboard_page)   # 0
        self.pages.addWidget(self.brokers_page)      # 1
        self.pages.addWidget(self.history_page)      # 2
        self.pages.addWidget(self.logs_page)         # 3
        self.pages.addWidget(self.settings_page)     # 4

        body_layout.addWidget(self.pages, 1)
        root_layout.addWidget(body, 1)

        # Select dashboard by default
        self.nav_buttons[0].setChecked(True)

    def _create_header(self):
        header = QFrame()
        header.setObjectName("header")
        header.setStyleSheet(themes.header_style())
        header.setFixedHeight(52)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 4, 16, 4)

        title = QLabel("EPCopyFlow 2.0")
        title.setProperty("class", "header-title")
        layout.addWidget(title)

        layout.addStretch()

        # System status labels
        self.internet_label = QLabel("Internet: --")
        self.internet_label.setProperty("class", "header-status")
        self.cpu_label = QLabel("CPU: --%")
        self.cpu_label.setProperty("class", "header-status")
        self.cpu_label.setFixedWidth(90)
        self.mem_label = QLabel("RAM: --%")
        self.mem_label.setProperty("class", "header-status")
        self.mem_label.setFixedWidth(90)

        for lbl in (self.internet_label, self.cpu_label, self.mem_label):
            layout.addWidget(lbl)
            layout.addSpacing(12)

        # Emergency button
        self.emergency_btn = QPushButton("EMERGENCIA - Fechar Tudo")
        self.emergency_btn.setObjectName("emergency-btn")
        self.emergency_btn.clicked.connect(self._on_emergency)
        layout.addWidget(self.emergency_btn)

        return header

    def _create_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setStyleSheet(themes.sidebar_style())
        sidebar.setFixedWidth(200)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(2)

        pages = [
            ("Dashboard", 0),
            ("Corretoras", 1),
            ("Historico", 2),
            ("Logs", 3),
            ("Configuracoes", 4),
        ]

        self.nav_buttons = []
        for label, index in pages:
            btn = QPushButton(label)
            btn.setProperty("class", "nav-btn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, idx=index: self._navigate(idx))
            layout.addWidget(btn)
            self.nav_buttons.append(btn)

        layout.addStretch()

        # Version label at bottom
        self.version_label = QLabel("v0.0.1")
        self.version_label.setStyleSheet(themes.version_label_style())
        layout.addWidget(self.version_label)

        return sidebar

    def _navigate(self, index):
        self.pages.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)

    # ── Theme ──
    def apply_theme(self):
        """Reaplica todos os estilos após troca de tema."""
        from PySide6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(themes.global_app_style())

        self.centralWidget().setStyleSheet(themes.main_area_style())
        self.header.setStyleSheet(themes.header_style())
        self.sidebar.setStyleSheet(themes.sidebar_style())
        self.pages.setStyleSheet(themes.page_background_style())
        self.version_label.setStyleSheet(themes.version_label_style())

        # Re-estilizar páginas
        self.dashboard_page.apply_theme()
        self.brokers_page.apply_theme()
        self.history_page.setStyleSheet(themes.history_page_style())
        self.logs_page.setStyleSheet(themes.logs_page_style())
        self.settings_page.apply_theme()

    # ── Signals ──
    def _connect_signals(self):
        self.zmq_message_handler.log_message_received.connect(self.logs_page.append_log)
        self.zmq_message_handler.log_message_received.connect(self._handle_zmq_messages)
        self.zmq_message_handler.positions_received.connect(self.dashboard_page.update_positions)
        self.zmq_message_handler.account_balance_received.connect(self.dashboard_page.update_balance)
        # Atualizar indicadores quando status muda
        self.zmq_message_handler.trade_allowed_update_received.connect(
            lambda _: self._update_all_indicators())
        self.zmq_message_handler.connection_status_received.connect(
            lambda _: self._update_all_indicators())
        # Sincronizar dashboard quando broker conecta/desconecta via botão
        self.brokers_page.broker_status_changed.connect(self.dashboard_page.refresh_brokers)
        if self.copytrade_manager:
            self.copytrade_manager.copy_trade_log.connect(self.logs_page.append_log)
            self.copytrade_manager.copy_trade_executed.connect(self.history_page.refresh)
            self.copytrade_manager.copy_trade_failed.connect(self.history_page.refresh)

    @Slot(str)
    def _handle_zmq_messages(self, message: str):
        status_changed = False
        for key in list(self.broker_status.keys()):
            if "REGISTER" in message and key in message and "UNREGISTER" not in message:
                self.broker_status[key] = True
                status_changed = True
                break
            elif ("CLIENT_UNREGISTERED" in message or "UNREGISTER" in message) and key in message:
                self.broker_status[key] = False
                status_changed = True
                break
        if status_changed:
            self.broker_status_updated.emit(self.broker_status, self.broker_modes)
            self.dashboard_page.refresh_brokers()
            self.brokers_page.refresh_brokers()

    def _update_all_indicators(self):
        """Update indicators on both dashboard and brokers page."""
        self.dashboard_page.update_broker_indicators()
        self.brokers_page.update_broker_indicators()

    # ── System Monitor ──
    @Slot(dict)
    def _on_system_status(self, status):
        online = status.get("internet", "Offline")
        color = themes.internet_status_color(online)
        self.internet_label.setText(f"Internet: <span style='color:{color}'>{online}</span>")
        self.cpu_label.setText(status.get("cpu", "CPU: --%"))
        self.mem_label.setText(status.get("memory", "RAM: --%"))

    # ── Emergency ──
    def _on_emergency(self):
        reply = QMessageBox.warning(
            self, "EMERGENCIA",
            "Fechar TODAS as posicoes em TODAS as corretoras (Master + Slaves)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            if self.copytrade_manager:
                asyncio.ensure_future(self.copytrade_manager.emergency_close_all())
                self.logs_page.append_log("EMERGENCIA: Fechando todas as posicoes...")
            else:
                QMessageBox.information(self, "Info", "CopyTradeManager nao inicializado.")

    # ── Window Events ──
    def showEvent(self, event):
        super().showEvent(event)
        logger.info("MainWindow exibida.")

    def closeEvent(self, event: QCloseEvent):
        logger.info("Fechando MainWindow...")
        self.indicators_timer.stop()
        self.internet_monitor.stop()

        # Desconectar todas as corretoras e fechar MT5
        try:
            for key in list(self.broker_manager.get_connected_brokers()):
                try:
                    self.broker_manager.disconnect_broker(key)
                    logger.info(f"MT5 desconectado para {key} no fechamento.")
                except Exception as e:
                    logger.error(f"Erro ao desconectar {key} no fechamento: {e}")
        except Exception as e:
            logger.error(f"Erro ao obter corretoras conectadas no fechamento: {e}")

        self.shutdown_event_ref.set()
        event.accept()
