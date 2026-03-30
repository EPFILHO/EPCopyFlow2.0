# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# main.py
# Ponto de entrada principal da aplicação EPCopyFlow 2.0.

import sys
import asyncio
import platform
import warnings
import qasync
import logging
import signal
import subprocess
import zmq.asyncio
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QProgressBar, QWidget
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from core.config_manager import ConfigManager
from core.broker_manager import BrokerManager
from core.zmq_router import ZmqRouter
from core.copytrade_manager import CopyTradeManager
from core.mt5_process_monitor import MT5ProcessMonitor
from gui.main_window import MainWindow
import os
from datetime import datetime

logger = logging.getLogger(__name__)


# ── Bloco 1 - Configuração Inicial ──
def configure_asyncio_policy():
    if platform.system() == "Windows":
        try:
            if sys.version_info < (3, 14):
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception as e:
            logger.warning(f"Falha ao definir WindowsSelectorEventLoopPolicy: {e}")


def filter_warnings():
    warnings.filterwarnings("ignore", category=RuntimeWarning, module="zmq.*")
    warnings.filterwarnings("ignore", message="not a socket")
    warnings.filterwarnings("ignore", message="Proactor event loop does not implement add_reader family of methods")


# ── Bloco 2 - Patch ZMQ ──
original_asyncpoller_poll = zmq.asyncio.Poller.poll


async def patched_asyncpoller_poll(self, timeout=None):
    try:
        return await original_asyncpoller_poll(self, timeout)
    except zmq.error.ZMQError as e:
        if "not a socket" in str(e):
            return []
        raise


def apply_zmq_patch():
    zmq.asyncio.Poller.poll = patched_asyncpoller_poll


# ── Bloco 3 - Logging ──
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


# ── Bloco 5 - Variáveis Globais ──
shutdown_event = asyncio.Event()
zmq_task = None
zmq_router_instance = None
mt5_processes = {}
broker_manager = None
mt5_monitor = None
copytrade_manager = None


# ── Bloco 6 - Encerramento ──
async def shutdown_cleanup():
    global zmq_task, zmq_router_instance, mt5_processes, broker_manager, mt5_monitor
    logger.info("Iniciando shutdown_cleanup...")

    if mt5_monitor:
        mt5_monitor.stop()
        logger.info("MT5ProcessMonitor parado.")

    if zmq_router_instance:
        try:
            await zmq_router_instance.stop()
        except Exception as e:
            logger.warning(f"Erro ao parar ZmqRouter: {e}")

    if zmq_task and not zmq_task.done():
        try:
            await asyncio.wait_for(zmq_task, timeout=2.0)
        except asyncio.TimeoutError:
            try:
                zmq_task.cancel()
            except Exception:
                pass
        except Exception as e:
            logger.exception(f"Erro ao esperar tarefa ZMQ: {e}")

    if broker_manager:
        try:
            for key in list(broker_manager.get_connected_brokers()):
                try:
                    broker_manager.disconnect_broker(key)
                except Exception as e:
                    logger.error(f"Erro ao desconectar {key}: {e}")
        except Exception as e:
            logger.error(f"Erro ao obter corretoras conectadas: {e}")

    for key, process in list(mt5_processes.items()):
        try:
            process.terminate()
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        except Exception as e:
            logger.error(f"Erro ao parar MT5 para {key}: {e}")
    mt5_processes.clear()

    logger.info("shutdown_cleanup concluído.")


def sigint_handler(*args):
    if not shutdown_event.is_set():
        shutdown_event.set()


# ── Bloco 7 - Fluxo Principal ──
async def show_splash_async(duration):
    """Exibe splash screen como coroutine sem bloquear o event loop."""
    app = QApplication.instance()
    splash_widget = QWidget()
    splash_widget.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    splash_widget.setFixedSize(400, 200)
    splash_widget.setStyleSheet("background-color: #1e1e2e;")
    layout = QVBoxLayout(splash_widget)

    title_label = QLabel("EPCopyFlow 2.0")
    title_label.setFont(QFont("Arial", 24, QFont.Bold))
    title_label.setStyleSheet("color: #89b4fa;")
    title_label.setAlignment(Qt.AlignCenter)
    layout.addWidget(title_label, 0, Qt.AlignCenter)

    subtitle_label = QLabel("CopyTrade Management Platform")
    subtitle_label.setFont(QFont("Arial", 12))
    subtitle_label.setStyleSheet("color: #cdd6f4;")
    subtitle_label.setAlignment(Qt.AlignCenter)
    layout.addWidget(subtitle_label, 0, Qt.AlignCenter)

    version_label = QLabel("v0.0.1")
    version_label.setFont(QFont("Arial", 10))
    version_label.setStyleSheet("color: #585b70;")
    version_label.setAlignment(Qt.AlignCenter)
    layout.addWidget(version_label, 0, Qt.AlignCenter)

    progress = QProgressBar()
    progress.setTextVisible(False)
    progress.setRange(0, 0)
    progress.setStyleSheet("""
        QProgressBar { background-color: #313244; border-radius: 4px; height: 6px; }
        QProgressBar::chunk { background-color: #89b4fa; border-radius: 4px; }
    """)
    layout.addWidget(progress)
    layout.setContentsMargins(20, 30, 20, 20)
    layout.setSpacing(10)

    screen = app.primaryScreen().geometry()
    splash_widget.move(
        (screen.width() - splash_widget.width()) // 2,
        (screen.height() - splash_widget.height()) // 2,
    )
    splash_widget.show()
    await asyncio.sleep(duration)
    splash_widget.close()


async def main_application_flow(config: ConfigManager):
    global zmq_task, zmq_router_instance, shutdown_event, mt5_processes
    global broker_manager, mt5_monitor, copytrade_manager
    logger.info("Iniciando EPCopyFlow 2.0...")

    # Splash screen (non-blocking)
    show_splash = config.getboolean('General', 'show_splash', fallback=True)
    if show_splash:
        splash_duration = config.getfloat('General', 'splash_duration', fallback=1.0)
        await show_splash_async(splash_duration)

    base_mt5_path = config.get('General', 'base_mt5_path', fallback='C:/Temp/MT5')
    root_path = os.path.dirname(os.path.abspath(__file__))

    zmq_router_instance = ZmqRouter(None)
    broker_manager = BrokerManager(config, base_mt5_path, root_path, zmq_router_instance)
    zmq_router_instance.broker_manager = broker_manager

    copytrade_manager = CopyTradeManager(broker_manager, zmq_router_instance)

    mt5_monitor = MT5ProcessMonitor(
        broker_manager,
        event_loop=asyncio.get_event_loop(),
        check_interval=config.getint('General', 'monitor_interval', fallback=10)
    )
    mt5_monitor.start()
    logger.info("MT5ProcessMonitor iniciado.")

    signal.signal(signal.SIGINT, sigint_handler)

    main_window = MainWindow(
        config, broker_manager, zmq_router_instance,
        shutdown_event, root_path, mt5_monitor, copytrade_manager
    )
    main_window.show()
    logger.info("MainWindow exibida.")

    # Wire copytrade_manager into message handler
    main_window.zmq_message_handler.set_copytrade_manager(copytrade_manager)

    zmq_task = asyncio.create_task(zmq_router_instance.run(main_window.zmq_message_handler))
    try:
        await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass

    logger.info("Setup concluído. Aguardando shutdown_event...")
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    logger.info("Sinal de encerramento recebido.")
    try:
        await shutdown_cleanup()
    except Exception as e:
        logger.error(f"Erro durante shutdown: {e}")

    logger.info("main_application_flow concluída.")


# ── Bloco 8 - Entry Point ──
if __name__ == "__main__":
    initial_app_config = ConfigManager()
    setup_logging(initial_app_config)
    logger.info("Iniciando EPCopyFlow 2.0.")

    configure_asyncio_policy()
    filter_warnings()
    apply_zmq_patch()

    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)

        # Use qasync's QEventLoop to properly integrate asyncio + Qt
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)

        with loop:
            loop.run_until_complete(main_application_flow(initial_app_config))

    except KeyboardInterrupt:
        if not shutdown_event.is_set():
            shutdown_event.set()
    except asyncio.CancelledError:
        pass
    except RuntimeError as e:
        if "Event loop stopped" not in str(e):
            logger.exception(f"Erro runtime: {e}")
    except Exception as e:
        logger.exception(f"Erro inesperado: {e}")
    finally:
        logger.info("Aplicação encerrada.")
