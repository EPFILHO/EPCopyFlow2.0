# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/mt5_process_monitor.py
# Monitor de processos MT5 - watchdog que reinicia instâncias que caírem.
# Proteção contra crash loop: max retries + backoff exponencial.

import os
import subprocess
import time
import logging
import asyncio
import threading

logger = logging.getLogger(__name__)


class MT5ProcessMonitor:
    def __init__(self, broker_manager, event_loop, config_manager=None, check_interval=10):
        self.broker_manager = broker_manager
        self.event_loop = event_loop
        self.check_interval = check_interval
        self.running = False
        self.monitor_thread = None

        # Retry config (do config.ini ou defaults)
        if config_manager:
            cfg = config_manager.config if hasattr(config_manager, 'config') else config_manager
            self.max_retries = cfg.getint('ProcessMonitor', 'max_retries', fallback=3)
            self.backoff_base = cfg.getint('ProcessMonitor', 'backoff_base', fallback=5)
            self.crash_window = cfg.getint('ProcessMonitor', 'crash_window', fallback=30)
        else:
            self.max_retries = 3
            self.backoff_base = 5
            self.crash_window = 30

        # Estado por broker
        self._retry_count = {}         # key -> número de restarts consecutivos
        self._last_restart_at = {}     # key -> timestamp do último restart
        self._last_alive_at = {}       # key -> timestamp da última vez visto vivo
        self._failed_brokers = set()   # brokers que esgotaram retries

        logger.info(f"MT5ProcessMonitor inicializado (max_retries={self.max_retries}, backoff_base={self.backoff_base}s, crash_window={self.crash_window}s).")

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

    def on_broker_registered(self, key):
        """Chamado quando EA envia REGISTER — MT5 voltou com sucesso."""
        retries = self._retry_count.get(key, 0)
        if retries > 0:
            logger.info(f"MT5 {key} reconectou após {retries} restart(s). Resetando contadores.")
        self._retry_count.pop(key, None)
        self._last_restart_at.pop(key, None)
        self._failed_brokers.discard(key)

    def check_and_restart_processes(self):
        now = time.time()

        for key in self.broker_manager.get_brokers():
            if not self.broker_manager.is_connected(key):
                continue

            if key in self._failed_brokers:
                continue

            process = self.broker_manager.mt5_processes.get(key)
            process_dead = (process is None) or (process.poll() is not None)

            if not process_dead:
                # Processo vivo — registrar timestamp
                self._last_alive_at[key] = now
                continue

            # --- Processo morto ---

            # Limpar processo morto do broker_manager
            if process and key in self.broker_manager.mt5_processes:
                exit_code = process.poll()
                del self.broker_manager.mt5_processes[key]
                self.broker_manager.connected_brokers[key] = False
            else:
                exit_code = "N/A"
                self.broker_manager.connected_brokers[key] = False

            # Detectar crash loop: se morreu rápido demais após último restart
            last_restart = self._last_restart_at.get(key)
            if last_restart and (now - last_restart) < self.crash_window:
                # Morreu dentro da janela de crash — incrementar retry
                retries = self._retry_count.get(key, 0)
                self._retry_count[key] = retries + 1
            elif last_restart is None and key in self._retry_count:
                # Já tem retries contados (continuação)
                pass
            else:
                # Primeira morte ou morreu após funcionar normalmente — reset retries
                self._retry_count[key] = 0

            retries = self._retry_count.get(key, 0)

            # Esgotou tentativas?
            if retries >= self.max_retries:
                logger.error(f"MT5 {key} — esgotou {self.max_retries} tentativas de restart (crash loop detectado). Desistindo.")
                self._failed_brokers.add(key)
                self._retry_count.pop(key, None)
                self._last_restart_at.pop(key, None)
                continue

            # Calcular backoff se não é primeira tentativa
            if retries > 0:
                backoff = self.backoff_base * (2 ** (retries - 1))  # 5, 10, 20...
                if last_restart and (now - last_restart) < backoff:
                    remaining = backoff - (now - last_restart)
                    logger.debug(f"MT5 {key} — backoff: {remaining:.0f}s antes do retry #{retries + 1}.")
                    # Re-adicionar processo como None para não perder o tracking
                    return
                logger.warning(f"MT5 {key} morreu (exit={exit_code}). Retry {retries + 1}/{self.max_retries} (backoff={backoff}s)...")
            else:
                logger.warning(f"MT5 {key} morreu (exit={exit_code}). Reiniciando imediatamente...")

            # Reiniciar
            success = self.restart_mt5_instance(key)
            self._last_restart_at[key] = time.time()

            if success:
                logger.info(f"MT5 {key} — restart enviado (tentativa {retries + 1}/{self.max_retries}).")
            else:
                logger.error(f"MT5 {key} — falha no restart (tentativa {retries + 1}/{self.max_retries}).")
                self._retry_count[key] = retries + 1

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
