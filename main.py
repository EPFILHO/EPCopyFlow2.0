# EPCopyFlow 2.0
# main.py
# Ponto de entrada principal da aplicação EPCopyFlow 2.0.
#
# Arquitetura (issue #111):
#   Main thread (Qt) ←─ signals (QueuedConnection auto cross-thread) ─→ Engine thread (asyncio)
#
#   Main thread executa app.exec() padrão. O motor de trade (TcpRouter,
#   CopyTradeManager, TcpMessageHandler) é construído e roda DENTRO do
#   EngineThread. Os QObjects do motor nascem com thread affinity correta
#   porque são criados dentro de uma coroutine submetida à engine.

import sys
import asyncio
import warnings
import logging
import signal
import os
from datetime import datetime

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QProgressBar, QWidget
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QTimer

from core.config_manager import ConfigManager
from core.broker_manager import BrokerManager
from core.tcp_router import TcpRouter
from core.copytrade_manager import CopyTradeManager
from core.tcp_message_handler import TcpMessageHandler
from core.mt5_process_monitor import MT5ProcessMonitor
from core.engine_thread import EngineThread
from core.latency_tracker import LatencyTracker, set_tracker
from core.version import __version__
from gui.main_window import MainWindow
from gui import themes

logger = logging.getLogger(__name__)


# ── Bloco 1 - Configuração Inicial ──
def filter_warnings():
    warnings.filterwarnings("ignore", message="not a socket")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="asyncio.*")


# ── Bloco 2 - Logging ──
class ColoredFormatter(logging.Formatter):
    BLUE = "\x1b[34;20m"
    LIME = "\x1b[92m"
    FUCHSIA = "\x1b[95m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"

    FORMATS = {
        logging.DEBUG: BLUE + "%(asctime)s - %(levelname)s - %(filename)s - %(message)s" + RESET,
        logging.INFO: LIME + "%(asctime)s - %(levelname)s - %(filename)s - %(message)s" + RESET,
        logging.WARNING: FUCHSIA + "%(asctime)s - %(levelname)s - %(filename)s - %(message)s" + RESET,
        logging.ERROR: RED + "%(asctime)s - %(levelname)s - %(filename)s - %(message)s" + RESET,
        logging.CRITICAL: BOLD_RED + "%(asctime)s - %(levelname)s - %(filename)s - %(message)s" + RESET,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        return logging.Formatter(log_fmt).format(record)


def setup_logging(config_manager_instance: ConfigManager):
    logs_dir = "logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = os.path.join(logs_dir, f"epcopyflow_{timestamp}.log")

    log_level_str = config_manager_instance.get('General', 'log_level', fallback='INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    file_handler = logging.FileHandler(log_filename, mode='w', encoding='utf-8')
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(filename)s - %(message)s"))
    logging.root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(ColoredFormatter())
    logging.root.addHandler(stream_handler)

    logging.root.setLevel(log_level)
    logger.info(f"Logging configurado. Log file: {log_filename}")


# ── Bloco 3 - Splash Screen (QTimer-based, sem await) ──
def show_splash(duration_seconds: float, on_close):
    """Exibe splash screen na main thread (Qt). Após `duration_seconds`, fecha
    o splash e chama `on_close()`. Não bloqueia — usa QTimer.singleShot.
    """
    s = themes.splash_style()
    app = QApplication.instance()
    splash_widget = QWidget()
    splash_widget.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    splash_widget.setFixedSize(400, 200)
    splash_widget.setStyleSheet(s["background"])
    layout = QVBoxLayout(splash_widget)

    title_label = QLabel("EPCopyFlow 2.0")
    title_label.setFont(QFont("Arial", 24, QFont.Bold))
    title_label.setStyleSheet(s["title"])
    title_label.setAlignment(Qt.AlignCenter)
    layout.addWidget(title_label, 0, Qt.AlignCenter)

    subtitle_label = QLabel("CopyTrade Management Platform")
    subtitle_label.setFont(QFont("Arial", 12))
    subtitle_label.setStyleSheet(s["subtitle"])
    subtitle_label.setAlignment(Qt.AlignCenter)
    layout.addWidget(subtitle_label, 0, Qt.AlignCenter)

    version_label = QLabel(f"v{__version__}")
    version_label.setFont(QFont("Arial", 10))
    version_label.setStyleSheet(s["version"])
    version_label.setAlignment(Qt.AlignCenter)
    layout.addWidget(version_label, 0, Qt.AlignCenter)

    progress = QProgressBar()
    progress.setTextVisible(False)
    progress.setRange(0, 0)
    progress.setStyleSheet(s["progress"])
    layout.addWidget(progress)
    layout.setContentsMargins(20, 30, 20, 20)
    layout.setSpacing(10)

    screen = app.primaryScreen().geometry()
    splash_widget.move(
        (screen.width() - splash_widget.width()) // 2,
        (screen.height() - splash_widget.height()) // 2,
    )
    splash_widget.show()

    def _close_and_continue():
        splash_widget.close()
        on_close()

    QTimer.singleShot(int(duration_seconds * 1000), _close_and_continue)


# ── Bloco 4 - Bootstrap do Motor (roda DENTRO do EngineThread) ──
async def bootstrap_engine(broker_manager: BrokerManager, config: ConfigManager):
    """
    Coroutine submetida ao EngineThread no startup. Constrói os QObjects do
    motor (TcpRouter, CopyTradeManager, TcpMessageHandler) na thread do motor
    para que a thread affinity do Qt fique correta. Retorna os componentes
    já wired-up (mas o tcp_router.run() ainda não foi iniciado — quem inicia
    é o passo seguinte).
    """
    tcp_router = TcpRouter(broker_manager)
    broker_manager.tcp_router = tcp_router

    copytrade_manager = CopyTradeManager(broker_manager, tcp_router)

    tcp_message_handler = TcpMessageHandler(
        config, tcp_router,
        broker_manager=broker_manager,
        copytrade_manager=copytrade_manager,
    )

    return tcp_router, copytrade_manager, tcp_message_handler


# ── Bloco 5 - Entry Point ──
def main():
    initial_app_config = ConfigManager()
    setup_logging(initial_app_config)
    logger.info(f"Iniciando EPCopyFlow 2.0 v{__version__}.")
    filter_warnings()

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    # Tema + stylesheet global aplicado cedo (cobre QMessageBox, etc.)
    saved_theme = initial_app_config.get('GUI', 'theme', fallback='Escuro')
    themes.set_theme(saved_theme)
    app.setStyleSheet(themes.global_app_style())

    # ── Iniciar LatencyTracker (instrumentação para diagnóstico do lag #111) ──
    # Não bloqueia hot path; CSV em logs/latency_<timestamp>.csv.
    latency_tracker = LatencyTracker()
    latency_tracker.start()
    set_tracker(latency_tracker)

    # ── Iniciar EngineThread ──
    engine = EngineThread(name="AsyncEngine")
    engine.start()
    logger.info("EngineThread iniciado.")

    # ── Construir BrokerManager (main thread; consumido pela GUI) ──
    base_mt5_path = initial_app_config.get('General', 'base_mt5_path', fallback='C:/Temp/MT5')
    root_path = os.path.dirname(os.path.abspath(__file__))
    broker_manager = BrokerManager(
        initial_app_config, base_mt5_path, root_path,
        tcp_router=None,   # preenchido pelo bootstrap_engine
        engine=engine,
    )

    # ── Bootstrap dos QObjects do motor DENTRO do engine thread ──
    bootstrap_fut = engine.submit(bootstrap_engine(broker_manager, initial_app_config))
    try:
        tcp_router, copytrade_manager, tcp_message_handler = bootstrap_fut.result(timeout=10.0)
    except Exception as e:
        logger.exception(f"Falha no bootstrap do motor: {e}")
        engine.stop(timeout=2.0)
        sys.exit(1)

    # Wire engine no tcp_message_handler (precisa para os send_* da GUI).
    tcp_message_handler.engine = engine
    tcp_message_handler.set_copytrade_manager(copytrade_manager)
    logger.info("Bootstrap do motor concluído (TcpRouter, CopyTradeManager, TcpMessageHandler).")

    # ── MT5 process monitor (thread própria, recebe loop do motor) ──
    mt5_monitor = MT5ProcessMonitor(
        broker_manager,
        event_loop=engine.loop,
        config_manager=initial_app_config,
        check_interval=initial_app_config.getint('General', 'monitor_interval', fallback=10),
    )
    mt5_monitor.start()
    tcp_message_handler.mt5_monitor = mt5_monitor
    logger.info("MT5ProcessMonitor iniciado.")

    # ── SIGINT / Ctrl+C: pede ao Qt para sair (closeEvent faz o teardown) ──
    def _sigint(*_args):
        logger.info("SIGINT recebido — solicitando saída.")
        QApplication.instance().quit()
    signal.signal(signal.SIGINT, _sigint)

    # ── Construir MainWindow (recebe handler já construído + engine) ──
    main_window = MainWindow(
        config=initial_app_config,
        broker_manager=broker_manager,
        tcp_router=tcp_router,
        engine=engine,
        root_path=root_path,
        mt5_monitor=mt5_monitor,
        copytrade_manager=copytrade_manager,
        tcp_message_handler=tcp_message_handler,
    )

    # ── Iniciar TcpRouter no motor (em background dentro do engine loop) ──
    # tcp_router.run() é uma coroutine que fica viva até stop() ser chamado.
    # Submetemos e seguimos — o Future fica vivo até o teardown.
    router_future = engine.submit(tcp_router.run(tcp_message_handler))
    logger.info("TcpRouter.run() submetido ao motor.")

    # ── Detecção de account modes em background ──
    engine.submit(copytrade_manager.detect_all_account_modes())

    # ── Splash + show window ──
    def _after_splash():
        main_window.show()
        logger.info("MainWindow exibida.")

    show_splash_flag = initial_app_config.getboolean('General', 'show_splash', fallback=True)
    if show_splash_flag:
        splash_duration = initial_app_config.getfloat('General', 'splash_duration', fallback=1.0)
        show_splash(splash_duration, _after_splash)
    else:
        _after_splash()

    # ── Event loop Qt ──
    try:
        exit_code = app.exec()
    except KeyboardInterrupt:
        exit_code = 0

    # Drenar router_future após shutdown (closeEvent já parou o tcp_router).
    if not router_future.done():
        try:
            router_future.result(timeout=2.0)
        except Exception:
            pass

    # Parar instrumentação de latência (drena o que faltar no buffer).
    try:
        latency_tracker.stop(timeout=2.0)
    except Exception:
        logger.exception("Erro ao parar LatencyTracker.")

    logger.info("Aplicação encerrada.")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
