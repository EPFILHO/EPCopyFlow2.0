# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/zmq_router.py
# Roteador ZMQ simplificado: 2 sockets por broker (CommandSocket + EventSocket).

import zmq
import zmq.asyncio
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)


class ZmqRouter:
    # ──────────────────────────────────────────────
    # Bloco 1 - Inicialização
    # ──────────────────────────────────────────────
    def __init__(self, broker_manager):
        self.broker_manager = broker_manager
        self._running = False
        self._message_handler = None
        self._clients = {}
        self._responses = {}
        self._response_events = {}
        self.context = zmq.asyncio.Context()

        # 2 sockets por broker (simplificado de 5)
        self.command_sockets = {}   # DEALER - bidirecional (admin + trade)
        self.event_sockets = {}     # SUB - recebe eventos do EA

        self._socket_control_queue = asyncio.Queue()
        logger.debug("ZmqRouter inicializado (2 sockets por broker).")

    # ──────────────────────────────────────────────
    # Bloco 2 - Comandos de Controle (Connect/Disconnect)
    # ──────────────────────────────────────────────
    async def connect_broker_sockets(self, broker_key: str, broker_config: dict):
        await self._socket_control_queue.put(("CONNECT", broker_key, broker_config))

    async def disconnect_broker_sockets(self, broker_key: str):
        await self._socket_control_queue.put(("DISCONNECT", broker_key))

    # ──────────────────────────────────────────────
    # Bloco 3 - Parada do Router
    # ──────────────────────────────────────────────
    async def stop(self):
        logger.info("Parando ZmqRouter...")
        self._running = False

        for event in self._response_events.values():
            event.set()

        all_broker_keys = set(self.command_sockets.keys()) | set(self.event_sockets.keys())
        for broker_key in all_broker_keys:
            await self._socket_control_queue.put(("DISCONNECT", broker_key))

        try:
            await asyncio.wait_for(self._socket_control_queue.join(), timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass

        self.context.term()
        logger.info("Contexto ZMQ terminado.")

    # ──────────────────────────────────────────────
    # Bloco 4 - Envio de Comandos
    # ──────────────────────────────────────────────
    async def send_command_to_broker(self, broker_key: str, command: str,
                                     payload: dict = None, request_id: str = None):
        """Envia um comando via CommandSocket e aguarda resposta."""
        if not broker_key:
            return {"status": "ERROR", "message": "broker_key não fornecida."}

        if broker_key not in self.command_sockets or self.command_sockets[broker_key].closed:
            return {"status": "ERROR", "message": f"Corretora {broker_key} não conectada."}

        request_id = request_id or f"{command.lower()}_{broker_key}_{int(time.time())}"
        message = {
            "type": "REQUEST",
            "command": command,
            "request_id": request_id,
            "broker_key": broker_key,
        }
        if payload:
            message["payload"] = payload

        self._response_events[request_id] = asyncio.Event()
        try:
            message_str = json.dumps(message)
            await self.command_sockets[broker_key].send(message_str.encode('utf-8'), zmq.NOBLOCK)
            logger.debug(f"Comando {command} enviado para {broker_key} (id={request_id})")

            await asyncio.wait_for(self._response_events[request_id].wait(), timeout=5.0)
            if request_id in self._responses:
                response = self._responses.pop(request_id)
                return response
            return {"status": "ERROR", "message": "Resposta não recebida"}
        except asyncio.TimeoutError:
            logger.error(f"Timeout ao aguardar resposta de {command} para {broker_key}")
            return {"status": "ERROR", "message": "Timeout na resposta"}
        except Exception as e:
            logger.error(f"Erro ao enviar {command} para {broker_key}: {e}")
            return {"status": "ERROR", "message": str(e)}
        finally:
            self._response_events.pop(request_id, None)

    async def configure_heartbeat_interval(self, broker_key: str):
        """
        Configura o intervalo de heartbeat no EA.
        Lê do config.ini e envia SET_HEARTBEAT_INTERVAL.
        """
        try:
            import configparser
            config = configparser.ConfigParser()
            config.read("config.ini")

            # Intervalo em segundos → converter para ms
            interval_seconds = int(config.get("CopyTrade", "heartbeat_interval", fallback="5"))
            interval_ms = interval_seconds * 1000

            # Validar intervalo
            if interval_ms < 1000 or interval_ms > 600000:
                interval_ms = 5000  # Default
                logger.warning(f"Intervalo inválido, usando default: {interval_ms}ms")

            response = await self.send_command_to_broker(
                broker_key,
                "SET_HEARTBEAT_INTERVAL",
                {"heartbeat_interval_ms": interval_ms},
                f"set_heartbeat_{broker_key}_{int(time.time())}"
            )

            if response.get("status") == "OK":
                logger.info(f"✅ Heartbeat configurado em {broker_key}: {interval_ms}ms")
            else:
                logger.warning(f"⚠️ Falha ao configurar heartbeat em {broker_key}: {response.get('error_message', '?')}")

        except Exception as e:
            logger.error(f"Erro ao configurar heartbeat em {broker_key}: {e}", exc_info=True)

    async def configure_magic_number(self, broker_key: str):
        """
        Configura o magic number no EA.
        Lê do config.ini e envia SET_MAGIC_NUMBER.
        """
        try:
            import configparser
            config = configparser.ConfigParser()
            config.read("config.ini")

            magic_number = int(config.get("CopyTrade", "magic_number", fallback="0"))
            if magic_number <= 0:
                logger.debug(f"Magic number não configurado, pulando para {broker_key}")
                return

            response = await self.send_command_to_broker(
                broker_key,
                "SET_MAGIC_NUMBER",
                {"magic_number": magic_number},
                f"set_magic_{broker_key}_{int(time.time())}"
            )

            if response.get("status") == "OK":
                logger.info(f"Magic number configurado em {broker_key}: {magic_number}")
            else:
                logger.warning(f"Falha ao configurar magic number em {broker_key}: {response.get('error_message', '?')}")

        except Exception as e:
            logger.error(f"Erro ao configurar magic number em {broker_key}: {e}", exc_info=True)

    # ──────────────────────────────────────────────
    # Bloco 5 - Processamento de Mensagens (fast-path síncrono)
    # ──────────────────────────────────────────────
    def _process_message(self, message_data: dict, broker_key: str):
        """Fast-path síncrono: sinaliza respostas e atualiza estado.
        Processamento pesado é despachado em tasks de background."""
        msg_type = message_data.get("type")
        event = message_data.get("event")
        broker_key_msg = message_data.get("broker_key")
        request_id = message_data.get("request_id")

        # RESPONSE: sinalizar o Future/Event que send_command_to_broker está aguardando
        if msg_type == "RESPONSE" and request_id:
            self._responses[request_id] = message_data
            if request_id in self._response_events:
                self._response_events[request_id].set()

        # REGISTER/UNREGISTER: atualizar mapa de clientes (rápido)
        if msg_type == "SYSTEM" and event == "REGISTER" and broker_key_msg:
            self._clients[broker_key_msg] = broker_key
        elif msg_type == "SYSTEM" and event == "UNREGISTER" and broker_key_msg:
            self._clients.pop(broker_key_msg, None)

        # Despachar todo o processamento pesado (GUI, copytrade, etc.) em background
        if self._message_handler:
            asyncio.create_task(self._dispatch_message(broker_key, message_data))

    async def _dispatch_message(self, broker_key: str, message_data: dict):
        """Background task: processa mensagem no message_handler sem bloquear o receive loop."""
        try:
            msg_type = message_data.get("type")
            event = message_data.get("event")

            if msg_type == "SYSTEM" and event == "UNREGISTER":
                notification = {
                    "type": "INTERNAL",
                    "event": "CLIENT_UNREGISTERED",
                    "broker_key": message_data.get("broker_key"),
                    "zmq_id_hex": broker_key
                }
                await self._message_handler.handle_zmq_message(
                    broker_key.encode('utf-8'), notification)
            else:
                await self._message_handler.handle_zmq_message(
                    broker_key.encode('utf-8'), message_data)
        except Exception as e:
            logger.exception(f"Erro ao processar mensagem de {broker_key}: {e}")

    # ──────────────────────────────────────────────
    # Bloco 6 - Setup / Teardown de Sockets
    # ──────────────────────────────────────────────
    async def _setup_single_broker_sockets(self, broker_key: str, config: dict):
        logger.info(f"Configurando sockets para {broker_key}...")

        # Limpa estado residual
        self._clients.pop(broker_key, None)
        await self._teardown_single_broker_sockets(broker_key)

        # CommandSocket (DEALER - bidirecional)
        command_port = config.get('command_port')
        if command_port:
            address = f"tcp://127.0.0.1:{command_port}"
            sock = self.context.socket(zmq.DEALER)
            try:
                sock.connect(address)
                sock.setsockopt(zmq.LINGER, 0)
                self.command_sockets[broker_key] = sock
                logger.info(f"CommandSocket conectado em {address} para {broker_key}")
            except zmq.ZMQError as e:
                logger.error(f"Erro ao conectar CommandSocket para {broker_key}: {e}")
                sock.close()
        else:
            logger.warning(f"command_port não definida para {broker_key}")

        # EventSocket (SUB - recebe eventos do EA)
        event_port = config.get('event_port')
        if event_port:
            address = f"tcp://127.0.0.1:{event_port}"
            sock = self.context.socket(zmq.SUB)
            try:
                sock.connect(address)
                sock.setsockopt_string(zmq.SUBSCRIBE, "")
                sock.setsockopt(zmq.LINGER, 0)
                self.event_sockets[broker_key] = sock
                logger.info(f"EventSocket conectado em {address} para {broker_key}")
            except zmq.ZMQError as e:
                logger.error(f"Erro ao conectar EventSocket para {broker_key}: {e}")
                sock.close()
        else:
            logger.warning(f"event_port não definida para {broker_key}")

    async def _teardown_single_broker_sockets(self, broker_key: str):
        logger.info(f"Desconectando sockets para {broker_key}...")
        for name, socket_dict in [
            ('CommandSocket', self.command_sockets),
            ('EventSocket', self.event_sockets),
        ]:
            sock = socket_dict.pop(broker_key, None)
            if sock and not sock.closed:
                try:
                    sock.close()
                    logger.debug(f"{name} para {broker_key} fechado.")
                except zmq.ZMQError as e:
                    logger.warning(f"Erro ao fechar {name} para {broker_key}: {e}")

    # ──────────────────────────────────────────────
    # Bloco 7 - Loop Principal (zmq.asyncio + Poller)
    # ──────────────────────────────────────────────
    async def _receive_loop(self):
        """Loop principal usando zmq.asyncio.Poller.
        Reage instantaneamente a mensagens (sem sleep fixo).
        Processamento pesado é despachado em background tasks."""
        logger.info("Loop de recebimento ZMQ iniciado (async poller).")
        self._running = True

        while self._running:
            try:
                # 1. Processar comandos de controle (CONNECT/DISCONNECT)
                while True:
                    try:
                        cmd_type, broker_key, *args = self._socket_control_queue.get_nowait()
                        if cmd_type == "CONNECT":
                            await self._setup_single_broker_sockets(broker_key, args[0])
                        elif cmd_type == "DISCONNECT":
                            await self._teardown_single_broker_sockets(broker_key)
                        self._socket_control_queue.task_done()
                    except asyncio.QueueEmpty:
                        break

                # 2. Construir poller com sockets atuais
                poller = zmq.asyncio.Poller()
                socket_map = {}  # socket → (broker_key, port_name)

                for bk, sock in list(self.command_sockets.items()):
                    if not sock.closed:
                        poller.register(sock, zmq.POLLIN)
                        socket_map[sock] = (bk, 'CommandSocket')
                for bk, sock in list(self.event_sockets.items()):
                    if not sock.closed:
                        poller.register(sock, zmq.POLLIN)
                        socket_map[sock] = (bk, 'EventSocket')

                if not socket_map:
                    # Sem sockets ativos — esperar brevemente
                    await asyncio.sleep(0.1)
                    continue

                # 3. Poll — suspende até chegarem dados ou timeout (50ms)
                events = await poller.poll(timeout=50)

                # 4. Drenar todas as mensagens dos sockets prontos
                for sock, _ in events:
                    info = socket_map.get(sock)
                    if not info:
                        continue
                    bk, port_name = info

                    while not sock.closed:
                        try:
                            raw = await sock.recv(zmq.NOBLOCK)
                            msg_str = raw.decode('utf-8', errors='ignore')
                            # Correção de JSON malformado (DEALER pode prefixar frame vazio)
                            if not msg_str.startswith('{'):
                                msg_str = '{' + msg_str
                            if not msg_str.endswith('}'):
                                msg_str += '}'
                            data = json.loads(msg_str)
                            self._process_message(data, bk)
                        except zmq.Again:
                            break  # Socket drenado
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON inválido de {bk} ({port_name}): {e}")
                        except Exception as e:
                            logger.exception(f"Erro ao receber de {bk} ({port_name}): {e}")
                            break

            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    break
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Erro no loop _receive_loop: {e}")
                await asyncio.sleep(0.5)

        logger.info("Loop de recebimento ZMQ finalizado.")

    async def run(self, message_handler):
        logger.info("ZmqRouter.run() iniciado.")
        self._message_handler = message_handler
        self._running = True
        try:
            await self._receive_loop()
        except asyncio.CancelledError:
            self._running = False
        except Exception as e:
            logger.exception(f"Erro crítico em ZmqRouter.run(): {e}")
            self._running = False
        finally:
            logger.info("ZmqRouter.run() finalizado.")
