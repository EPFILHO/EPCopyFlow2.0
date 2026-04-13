# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# internet_monitor.py
# Monitor de internet e sistema usando QTimer (thread-safe, roda na GUI thread).

import logging
import psutil
from PySide6.QtCore import QTimer, QObject, Signal

logger = logging.getLogger(__name__)


class InternetMonitor(QObject):
    """Monitor de internet e sistema que roda na thread do Qt via QTimer."""
    status_updated = Signal(dict)

    def __init__(self, status_callback=None, check_interval=5, parent=None):
        super().__init__(parent)
        self.check_interval = check_interval
        self.internet_status = False

        # Conecta signal ao callback se fornecido
        if status_callback:
            self.status_updated.connect(status_callback)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check)

    def is_online(self):
        try:
            stats = psutil.net_if_stats()
            return any(
                s.isup for name, s in stats.items()
                if not name.lower().startswith('lo')
            )
        except Exception:
            return False

    def get_system_info(self):
        try:
            cpu = psutil.cpu_percent(interval=0)
            memory = psutil.virtual_memory().percent
            return cpu, memory
        except Exception as e:
            logger.error(f"Erro ao obter info do sistema: {e}")
            return 0, 0

    def start(self):
        self.timer.start(self.check_interval * 1000)
        # Primeira verificação imediata
        QTimer.singleShot(100, self._check)
        logger.info("InternetMonitor iniciado (QTimer).")

    def stop(self):
        self.timer.stop()
        logger.info("InternetMonitor parado.")

    def _check(self):
        new_status = self.is_online()
        cpu, memory = self.get_system_info()

        if new_status != self.internet_status:
            self.internet_status = new_status
            logger.info(f"Internet {'Online' if new_status else 'Offline'}")

        status = {
            "internet": "Online" if self.internet_status else "Offline",
            "cpu": f"CPU: {cpu:.1f}%",
            "memory": f"RAM: {memory:.1f}%",
        }

        self.status_updated.emit(status)
