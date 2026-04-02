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
    def __init__(self, broker_manager, event_loop, config_manager=None, check_interval=10):
        self.broker_manager = broker_manager
        self.event_loop = event_loop
        self.check_interval = check_interval
        self.running = False
        self.monitor_thread = None

        # Grace period e retry config (do config.ini ou defaults)
        if config_manager:
            cfg = config_manager.config if hasattr(config_manager, 'config') else config_manager
            self.grace_period = cfg.getint('ProcessMonitor', 'grace_period', fallback=60)
            self.max_retries = cfg.getint('ProcessMonitor', 'max_retries', fallback=3)
            self.backoff_base = cfg.getint('ProcessMonitor', 'backoff_base', fallback=5)
        else:
            self.grace_period = 60
            self.max_retries = 3
            self.backoff_base = 5

        # Estado por broker: rastreia grace period e retries
        self._death_detected_at = {}   # key -> timestamp da primeira detecção de morte
        self._retry_count = {}         # key -> número de restarts tentados
        self._failed_brokers = set()   # brokers que esgotaram retries

        logger.info(f"MT5ProcessMonitor inicializado (grace={self.grace_period}s, max_retries={self.max_retries}, backoff_base={self.backoff_base}s).")

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
        """Chamado quando EA envia REGISTER - cancela grace period/retry."""
        if key in self._death_detected_at:
            elapsed = time.time() - self._death_detected_at[key]
            logger.info(f"MT5 {key} reconectou após {elapsed:.0f}s (provável update). Cancelando restart.")
            del self._death_detected_at[key]
        self._retry_count.pop(key, None)
        self._failed_brokers.discard(key)

    def check_and_restart_processes(self):
        for key in self.broker_manager.get_brokers():
            if not self.broker_manager.is_connected(key):
                continue

            if key in self._failed_brokers:
                continue

            process = self.broker_manager.mt5_processes.get(key)
            process_dead = False

            if process:
                if process.poll() is not None:
                    process_dead = True
            else:
                process_dead = True

            if not process_dead:
                # Processo vivo - limpar estado se tinha morte detectada
                if key in self._death_detected_at:
                    logger.info(f"MT5 {key} voltou sozinho. Cancelando grace period.")
                    del self._death_detected_at[key]
                    self._retry_count.pop(key, None)
                continue

            # --- Processo morto ---
            now = time.time()

            # Primeira detecção de morte? Iniciar grace period
            if key not in self._death_detected_at:
                exit_code = process.poll() if process else "N/A"
                logger.warning(f"MT5 {key} morreu (exit={exit_code}). Aguardando grace period de {self.grace_period}s (possível update)...")
                self._death_detected_at[key] = now
                if process and key in self.broker_manager.mt5_processes:
                    del self.broker_manager.mt5_processes[key]
                self.broker_manager.connected_brokers[key] = False
                continue

            # Ainda dentro do grace period? Esperar
            elapsed = now - self._death_detected_at[key]
            if elapsed < self.grace_period:
                remaining = self.grace_period - elapsed
                logger.debug(f"MT5 {key} — grace period: {remaining:.0f}s restantes.")
                continue

            # Grace period expirou — tentar restart
            retries = self._retry_count.get(key, 0)
            if retries >= self.max_retries:
                logger.error(f"MT5 {key} — esgotou {self.max_retries} tentativas de restart. Desistindo.")
                self._failed_brokers.add(key)
                self._death_detected_at.pop(key, None)
                self.broker_manager.connected_brokers[key] = False
                continue

            # Backoff exponencial entre retries
            if retries > 0:
                backoff = self.backoff_base * (2 ** (retries - 1))
                time_since_death = elapsed - self.grace_period
                if time_since_death < backoff * retries:
                    logger.debug(f"MT5 {key} — backoff: aguardando antes do retry #{retries + 1}.")
                    continue

            logger.warning(f"MT5 {key} — tentativa de restart {retries + 1}/{self.max_retries}...")
            success = self.restart_mt5_instance(key)
            self._retry_count[key] = retries + 1

            if success:
                logger.info(f"MT5 {key} — restart enviado (tentativa {retries + 1}). Aguardando REGISTER do EA...")
            else:
                logger.error(f"MT5 {key} — falha no restart (tentativa {retries + 1}/{self.max_retries}).")

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
