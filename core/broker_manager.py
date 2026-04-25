# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/broker_manager.py
# Gerenciador de corretoras com suporte a roles (master/slave),
# multiplicador de lote, e comunicação TCP nativa (1 porta por broker).

import json
import os
import shutil
import logging
import subprocess
import sys
import threading
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class BrokerManager(QObject):
    brokers_updated = Signal()

    # ──────────────────────────────────────────────
    # Bloco 1 - Inicialização
    # ──────────────────────────────────────────────
    def __init__(self, config, base_mt5_path, root_path, tcp_router, engine=None):
        """
        engine: instância de core.engine_thread.EngineThread. Usada para submeter
        coroutines de connect/disconnect de sockets ao loop do motor a partir de
        callers da GUI (que rodam na main thread, sem event loop asyncio próprio).
        Pode ser None em testes ou em fluxos puramente síncronos.
        """
        super().__init__()
        self.brokers_file = config.get('General', 'brokers_file', fallback='brokers.json')
        self.base_mt5_path = base_mt5_path
        self.root_path = root_path
        self.instances_dir = os.path.join(self.root_path, ".mt5_instances")
        self.brokers = self.load_brokers()
        self.connected_brokers = {}
        self.mt5_processes = {}
        self.tcp_router = tcp_router
        self.engine = engine

        # Reentrant lock: alguns fluxos chamam métodos públicos a partir de
        # outros que já seguram o lock (ex.: modify_broker → disconnect_broker).
        self._state_lock = threading.RLock()

        logger.debug("BrokerManager inicializado.")

    # ──────────────────────────────────────────────
    # Bloco 2 - Load / Save
    # ──────────────────────────────────────────────
    def _submit_to_engine(self, coro):
        """Submete uma coroutine ao loop do motor. Se não há engine configurado,
        loga e ignora (ambiente de teste)."""
        if self.engine is None:
            logger.warning("BrokerManager sem engine — coroutine ignorada.")
            coro.close()
            return None
        return self.engine.submit(coro)

    def load_brokers(self):
        try:
            if os.path.exists(self.brokers_file):
                with open(self.brokers_file, 'r') as f:
                    brokers = json.load(f)
                    self.connected_brokers = {key: False for key in brokers}
                    # Migração: garantir que todos os brokers tenham os novos campos
                    for key, data in brokers.items():
                        if "role" not in data:
                            data["role"] = "slave"
                        if "lot_multiplier" not in data:
                            data["lot_multiplier"] = 1.0
                        # Migração de 5 portas para 2
                        if "command_port" not in data and "admin_port" in data:
                            data["command_port"] = data.pop("admin_port")
                            data["event_port"] = data.pop("live_port", data["command_port"] + 1)
                            data.pop("data_port", None)
                            data.pop("trade_port", None)
                            data.pop("str_port", None)
                            data.pop("stream_port", None)
                    logger.info(f"Corretoras carregadas: {len(brokers)}.")
                    return brokers
            logger.info("Arquivo de corretoras não encontrado. Retornando vazio.")
            return {}
        except Exception as e:
            logger.error(f"Erro ao carregar corretoras: {e}")
            return {}

    def save_brokers(self):
        try:
            with open(self.brokers_file, 'w') as f:
                json.dump(self.brokers, f, indent=4)
            logger.info("Corretoras salvas.")
        except Exception as e:
            logger.error(f"Erro ao salvar corretoras: {e}")

    # ──────────────────────────────────────────────
    # Bloco 3 - CRUD de Corretoras
    # ──────────────────────────────────────────────
    def add_broker(self, name, broker_name, login, password, server,
                   command_port, event_port,
                   client="", mode="", type_="",
                   role="slave", lot_multiplier=1.0):
        key = f"{broker_name.upper()}-{login}"
        if key in self.brokers:
            logger.error(f"Corretora {key} já existe.")
            return None

        if role == "master" and self.get_master_broker():
            logger.error("Já existe um master definido. Só é permitido um.")
            return None

        instance_path = self.setup_portable_instance(key)
        if not instance_path:
            return None

        self.brokers[key] = {
            "name": name,
            "client": client,
            "broker_name": broker_name,
            "login": login,
            "password": password,
            "server": server,
            "type": type_,
            "mode": mode,
            "role": role,
            "lot_multiplier": lot_multiplier,
            "command_port": command_port,
            "event_port": event_port,
        }
        self.save_brokers()
        with self._state_lock:
            self.connected_brokers[key] = False
        self.create_mt5_config(key)
        logger.info(f"Corretora {key} adicionada (role={role}).")
        self.brokers_updated.emit()
        return key

    def remove_broker(self, key):
        if key not in self.brokers:
            logger.error(f"Corretora {key} não encontrada.")
            return False

        if self.is_connected(key):
            self.disconnect_broker(key)

        del self.brokers[key]
        self.save_brokers()
        with self._state_lock:
            self.connected_brokers.pop(key, None)
        instance_path = os.path.join(self.instances_dir, key)
        if os.path.exists(instance_path):
            shutil.rmtree(instance_path, ignore_errors=True)
            logger.info(f"Diretório MT5 de {key} excluído.")
        logger.info(f"Corretora {key} removida.")
        self.brokers_updated.emit()
        return True

    def modify_broker(self, old_key, name, broker_name, login, password, server,
                      command_port, event_port,
                      client="", mode="", type_="",
                      role="slave", lot_multiplier=1.0):
        if old_key not in self.brokers:
            logger.error(f"Corretora {old_key} não encontrada.")
            return None

        if self.is_connected(old_key):
            self.disconnect_broker(old_key)

        # Validar master único
        if role == "master":
            current_master = self.get_master_broker()
            if current_master and current_master != old_key:
                logger.error(f"Já existe um master ({current_master}). Só é permitido um.")
                return None

        old_data = self.brokers.pop(old_key)
        if broker_name is None:
            broker_name = old_data.get("broker_name", old_key.split("-")[0])
        new_key = f"{broker_name.upper()}-{login}"

        if new_key != old_key and new_key in self.brokers:
            logger.error(f"Já existe corretora com chave {new_key}.")
            self.brokers[old_key] = old_data
            return None

        if new_key != old_key:
            old_instance_path = os.path.join(self.instances_dir, old_key)
            if os.path.exists(old_instance_path):
                shutil.rmtree(old_instance_path, ignore_errors=True)
            self.setup_portable_instance(new_key)

        self.brokers[new_key] = {
            "name": name,
            "client": client or old_data.get("client", ""),
            "broker_name": broker_name,
            "login": login,
            "password": password,
            "server": server,
            "type": type_ or old_data.get("type", ""),
            "mode": mode or old_data.get("mode", ""),
            "role": role,
            "lot_multiplier": lot_multiplier,
            "command_port": command_port,
            "event_port": event_port,
        }
        self.save_brokers()
        with self._state_lock:
            if old_key in self.connected_brokers:
                self.connected_brokers[new_key] = self.connected_brokers.pop(old_key)
            else:
                self.connected_brokers[new_key] = False
        self.create_mt5_config(new_key)
        logger.info(f"Corretora {old_key} modificada para {new_key} (role={role}).")
        self.brokers_updated.emit()
        return new_key

    # ──────────────────────────────────────────────
    # Bloco 3b - Consultas de Role
    # ──────────────────────────────────────────────
    def get_master_broker(self):
        """Retorna a broker_key do master, ou None se não houver."""
        for key, data in self.brokers.items():
            if data.get("role") == "master":
                return key
        return None

    def get_slave_brokers(self):
        """Retorna lista de broker_keys dos slaves."""
        return [key for key, data in self.brokers.items() if data.get("role") == "slave"]

    def get_broker_role(self, key):
        """Retorna o role de um broker (master/slave)."""
        return self.brokers.get(key, {}).get("role", "slave")

    def get_lot_multiplier(self, key):
        """Retorna o multiplicador de lote de um broker."""
        return self.brokers.get(key, {}).get("lot_multiplier", 1.0)

    def get_account_mode(self, key):
        """Retorna o modo da conta (Netting ou Hedge)."""
        return self.brokers.get(key, {}).get("mode", "Netting")

    def cache_detected_mode(self, key, mode):
        """Armazena o modo detectado da conta em brokers.json."""
        if key not in self.brokers:
            logger.warning(f"Corretora {key} não encontrada ao cachear modo detectado.")
            return

        self.brokers[key]["mode"] = mode
        self.save_brokers()
        logger.info(f"Modo detectado para {key}: {mode}")

    # ──────────────────────────────────────────────
    # Bloco 4 - Instâncias MT5 Portáteis
    # ──────────────────────────────────────────────
    def setup_portable_instance(self, key):
        instance_path = os.path.join(self.instances_dir, key)
        executable = os.path.join(instance_path, "terminal64.exe")
        if not os.path.exists(instance_path):
            try:
                os.makedirs(self.instances_dir, exist_ok=True)
                shutil.copytree(self.base_mt5_path, instance_path)
                self.copy_dlls(instance_path)
                self.copy_expert(instance_path)
                if sys.platform.startswith("win"):
                    import win32api, win32con
                    win32api.SetFileAttributes(instance_path, win32con.FILE_ATTRIBUTE_HIDDEN)
                logger.info(f"Instância MT5 criada para {key}.")
            except Exception as e:
                logger.error(f"Erro ao criar instância para {key}: {e}")
                return None
        return executable

    def copy_dlls(self, instance_path):
        source_dll_path = os.path.join(self.root_path, "dlls")
        dest_dll_path = os.path.join(instance_path, "MQL5", "Libraries")
        os.makedirs(dest_dll_path, exist_ok=True)
        try:
            for filename in os.listdir(source_dll_path):
                if filename.endswith(".dll"):
                    shutil.copy2(
                        os.path.join(source_dll_path, filename),
                        os.path.join(dest_dll_path, filename)
                    )
        except Exception as e:
            logger.error(f"Erro ao copiar DLLs: {e}")

    def copy_expert(self, instance_path):
        source = os.path.join(self.root_path, "mt5_ea", "EPCopyFlow2_EA.ex5")
        dest_dir = os.path.join(instance_path, "MQL5", "Experts")
        os.makedirs(dest_dir, exist_ok=True)
        try:
            shutil.copy2(source, dest_dir)
        except Exception as e:
            logger.error(f"Erro ao copiar Expert Advisor: {e}")

    def create_mt5_config(self, key):
        """Cria config.ini na pasta do MT5 com BrokerKey, Role e portas TCP.

        Nota: magic_number NÃO é mais gravado aqui. O Python é a fonte única
        e envia SET_MAGIC_NUMBER via socket logo após o EA registrar.
        """
        broker_data = self.brokers.get(key, {})
        instance_path = os.path.join(self.instances_dir, key)
        config_file_path = os.path.join(instance_path, "MQL5", "Files", "config.ini")

        role = broker_data.get("role", "slave").upper()
        command_port = broker_data.get("command_port", 15555)
        event_port = broker_data.get("event_port", 15556)

        lines = [
            "[General]",
            f"BrokerKey={key}",
            f"Role={role}",
            "[Ports]",
            f"CommandPort={command_port}",
            f"EventPort={event_port}",
        ]
        content = "\n".join(lines)
        try:
            os.makedirs(os.path.dirname(config_file_path), exist_ok=True)
            with open(config_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Config.ini criado para {key} (Role={role}).")
        except Exception as e:
            logger.error(f"Erro ao criar config.ini: {e}")

    # ──────────────────────────────────────────────
    # Bloco 5 - Conexão / Desconexão
    # ──────────────────────────────────────────────
    def get_brokers(self):
        return self.brokers

    def connect_broker(self, key):
        if key not in self.brokers:
            logger.error(f"Corretora {key} não encontrada.")
            return False

        # Já está rodando?
        with self._state_lock:
            existing = self.mt5_processes.get(key)
            already_running = existing is not None and existing.poll() is None
        if already_running:
            logger.warning(f"MT5 já em execução para {key}. Reconectando sockets.")
            if self.tcp_router:
                broker_config = self.brokers[key]
                self._submit_to_engine(
                    self.tcp_router.connect_broker_sockets(key, broker_config)
                )
            with self._state_lock:
                self.connected_brokers[key] = True
            self.brokers_updated.emit()
            return True

        instance_path = os.path.join(self.instances_dir, key, "terminal64.exe")
        if not os.path.exists(instance_path):
            logger.error(f"Instância MT5 não encontrada para {key}.")
            return False

        try:
            logger.info(f"Iniciando MT5 para {key}...")
            if sys.platform.startswith("win"):
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 6  # SW_MINIMIZE
                process = subprocess.Popen(
                    [instance_path, "/portable"],
                    cwd=os.path.dirname(instance_path),
                    startupinfo=si
                )
            else:
                process = subprocess.Popen(
                    [instance_path, "/portable"],
                    cwd=os.path.dirname(instance_path)
                )
            with self._state_lock:
                self.mt5_processes[key] = process
                self.connected_brokers[key] = True
            logger.info(f"MT5 iniciado para {key}.")

            if self.tcp_router:
                broker_config = self.brokers[key]
                self._submit_to_engine(
                    self.tcp_router.connect_broker_sockets(key, broker_config)
                )
            self.brokers_updated.emit()
            return True
        except Exception as e:
            logger.error(f"Erro ao iniciar MT5 para {key}: {e}")
            return False

    def disconnect_broker(self, key):
        if key not in self.brokers:
            logger.error(f"Corretora {key} não encontrada.")
            return False

        if self.tcp_router:
            self._submit_to_engine(self.tcp_router.disconnect_broker_sockets(key))

        with self._state_lock:
            process = self.mt5_processes.pop(key, None)

        if process is not None:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                except Exception as e:
                    logger.error(f"Erro ao parar MT5 para {key}: {e}")
                    with self._state_lock:
                        self.connected_brokers[key] = False
                    self.brokers_updated.emit()
                    return False

        with self._state_lock:
            self.connected_brokers[key] = False
        self.brokers_updated.emit()
        return True

    def is_connected(self, key):
        with self._state_lock:
            return self.connected_brokers.get(key, False)

    def get_connected_brokers(self):
        with self._state_lock:
            return [key for key, connected in self.connected_brokers.items() if connected]

    # ──────────────────────────────────────────────
    # Bloco 5b - Acessores thread-safe (usados por mt5_process_monitor)
    # ──────────────────────────────────────────────
    def set_mt5_process(self, key, process):
        """Registra um processo MT5 (usado por watchdog ao reiniciar instância)."""
        with self._state_lock:
            self.mt5_processes[key] = process

    def set_connected(self, key, value: bool):
        """Atualiza flag de conexão de um broker."""
        with self._state_lock:
            self.connected_brokers[key] = value

    def get_mt5_process(self, key):
        """Retorna o subprocess.Popen do MT5 de um broker (ou None)."""
        with self._state_lock:
            return self.mt5_processes.get(key)

    def get_connected_slave_brokers(self):
        """Retorna lista de slaves conectados."""
        return [key for key in self.get_connected_brokers()
                if self.get_broker_role(key) == "slave"]

    # ──────────────────────────────────────────────
    # Bloco 6 - Geração de Portas
    # ──────────────────────────────────────────────
    def generate_ports(self):
        """Gera um par de portas (command, event) não utilizadas."""
        base_port = 15555
        step = 2
        used_ports = set()
        for data in self.brokers.values():
            used_ports.add(data.get("command_port", 0))
            used_ports.add(data.get("event_port", 0))

        port = base_port
        while port < 65535:
            command_port = port
            event_port = port + 1
            if command_port not in used_ports and event_port not in used_ports:
                return command_port, event_port
            port += step
        raise RuntimeError("Sem portas disponíveis.")
