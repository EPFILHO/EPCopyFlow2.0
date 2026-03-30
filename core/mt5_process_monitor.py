# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/mt5_process_monitor.py
# Monitor de processos MT5 - watchdog que reinicia instâncias que caírem.

import os
import subprocess
import time
import logging
import asyncio
import threading

logger = logging.getLogger(__name__)


class MT5ProcessMonitor:
    def __init__(self, broker_manager, event_loop, check_interval=10):
        """
        Inicializa o monitor de processos MT5.

        Args:
            broker_manager (BrokerManager): Instância do BrokerManager.
            event_loop (asyncio.AbstractEventLoop): Loop de eventos asyncio principal.
            check_interval (int): Intervalo de verificação em segundos.
        """
        self.broker_manager = broker_manager
        self.event_loop = event_loop
        self.check_interval = check_interval
        self.running = False
        self.monitor_thread = None
        logger.info("MT5ProcessMonitor inicializado.")

    def start(self):
        if not self.monitor_thread or not self.monitor_thread.is_alive():
            self.running = True
            self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
            self.monitor_thread.start()
            logger.info("MT5ProcessMonitor iniciado.")
        else:
            logger.warning("MT5ProcessMonitor já está em execução.")

    def stop(self):
        if self.running:
            self.running = False
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=5)
                if self.monitor_thread.is_alive():
                    logger.warning("Thread de MT5ProcessMonitor não terminou após join.")
                else:
                    logger.info("Thread de MT5ProcessMonitor encerrada com sucesso.")
            logger.info("MT5ProcessMonitor parado.")

    def monitor_loop(self):
        while self.running:
            try:
                self.check_and_restart_processes()
            except Exception as e:
                logger.error(f"Erro no loop de monitoramento: {e}")
            time.sleep(self.check_interval)

    def check_and_restart_processes(self):
        for key in self.broker_manager.get_brokers():
            if not self.broker_manager.is_connected(key):
                continue

            process = self.broker_manager.mt5_processes.get(key)
            if process:
                if process.poll() is not None:
                    logger.warning(f"MT5 para {key} terminou (exit={process.poll()}). Reiniciando...")
                    del self.broker_manager.mt5_processes[key]
                    self.broker_manager.connected_brokers[key] = False
                    self.restart_mt5_instance(key)
            else:
                logger.warning(f"MT5 para {key} não encontrado mas marcado conectado. Reiniciando...")
                self.restart_mt5_instance(key)

    def restart_mt5_instance(self, key):
        instance_path = os.path.join(self.broker_manager.instances_dir, key, "terminal64.exe")
        if not os.path.exists(instance_path):
            logger.error(f"Instância MT5 não encontrada para {key}: {instance_path}")
            return False

        try:
            broker_config = self.broker_manager.brokers[key]
            if os.name == 'nt':
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 6  # SW_MINIMIZE
                process = subprocess.Popen(
                    [
                        instance_path,
                        "/portable",
                        f"/login:{broker_config['login']}",
                        f"/password:{broker_config['password']}",
                        f"/server:{broker_config['server']}"
                    ],
                    cwd=os.path.dirname(instance_path),
                    startupinfo=si
                )
            else:
                process = subprocess.Popen(
                    [
                        instance_path,
                        "/portable",
                        f"/login:{broker_config['login']}",
                        f"/password:{broker_config['password']}",
                        f"/server:{broker_config['server']}"
                    ],
                    cwd=os.path.dirname(instance_path)
                )
            self.broker_manager.mt5_processes[key] = process
            self.broker_manager.connected_brokers[key] = True
            logger.info(f"MT5 reiniciado para {key} (PID: {process.pid}).")

            if self.broker_manager.zmq_router:
                asyncio.run_coroutine_threadsafe(
                    self.broker_manager.zmq_router.connect_broker_sockets(key, broker_config),
                    self.event_loop
                )
            return True
        except Exception as e:
            logger.error(f"Erro ao reiniciar MT5 para {key}: {e}")
            self.broker_manager.connected_brokers[key] = False
            return False
