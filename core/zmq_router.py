# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/zmq_router.py
# Roteador ZMQ simplificado: 2 sockets por broker (CommandSocket + EventSocket).

import zmq
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
        self.context = zmq.Context()

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
            self.command_sockets[broker_key].send(message_str.encode('utf-8'), zmq.NOBLOCK)
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
    # Bloco 5 - Processamento de Mensagens
    # ──────────────────────────────────────────────
    async def _process_message(self, message_data: dict, broker_key: str):
        msg_type = message_data.get("type")
        event = message_data.get("event")
        broker_key_msg = message_data.get("broker_key")
        request_id = message_data.get("request_id")

        if msg_type == "SYSTEM" and event == "REGISTER":
            if broker_key_msg:
                self._clients[broker_key_msg] = broker_key
                if self._message_handler:
                    await self._message_handler.handle_zmq_message(
                        broker_key.encode('utf-8'), message_data)
        elif msg_type == "SYSTEM" and event == "UNREGISTER":
            removed_key = None
            if broker_key_msg and broker_key_msg in self._clients:
                del self._clients[broker_key_msg]
                removed_key = broker_key_msg
            if self._message_handler:
                notification = {
                    "type": "INTERNAL",
                    "event": "CLIENT_UNREGISTERED",
                    "broker_key": removed_key,
                    "zmq_id_hex": broker_key
                }
                await self._message_handler.handle_zmq_message(
                    broker_key.encode('utf-8'), notification)
        elif msg_type == "RESPONSE":
            if request_id:
                self._responses[request_id] = message_data
                if request_id in self._response_events:
                    self._response_events[request_id].set()
            if self._message_handler:
                await self._message_handler.handle_zmq_message(
                    broker_key.encode('utf-8'), message_data)
        elif msg_type == "STREAM":
            if self._message_handler:
                await self._message_handler.handle_zmq_message(
                    broker_key.encode('utf-8'), message_data)
        else:
            logger.warning(f"Mensagem não tratada de {broker_key}: {message_data}")
            if self._message_handler:
                await self._message_handler.handle_zmq_message(
                    broker_key.encode('utf-8'), message_data)

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
    # Bloco 7 - Loop Principal
    # ──────────────────────────────────────────────
    async def _receive_loop(self):
        logger.info("Loop de recebimento ZMQ iniciado.")
        self._running = True
        while self._running:
            try:
                # Processa comandos de controle
                while not self._socket_control_queue.empty():
                    cmd_type, broker_key, *args = await self._socket_control_queue.get()
                    if cmd_type == "CONNECT":
                        await self._setup_single_broker_sockets(broker_key, args[0])
                    elif cmd_type == "DISCONNECT":
                        await self._teardown_single_broker_sockets(broker_key)
                    self._socket_control_queue.task_done()

                # Processa mensagens dos sockets (polling não-bloqueante)
                had_messages = False
                for socket_map, port_name in [
                    (self.command_sockets, 'CommandSocket'),
                    (self.event_sockets, 'EventSocket'),
                ]:
                    for broker_key, socket in list(socket_map.items()):
                        if socket.closed:
                            continue
                        try:
                            while True:
                                raw = socket.recv(zmq.NOBLOCK)
                                had_messages = True
                                msg_str = raw.decode('utf-8', errors='ignore')
                                # Correção de JSON malformado
                                if not msg_str.startswith('{'):
                                    msg_str = '{' + msg_str
                                if not msg_str.endswith('}'):
                                    msg_str += '}'
                                data = json.loads(msg_str)
                                await self._process_message(data, broker_key)
                        except zmq.Again:
                            pass  # Sem mensagens pendentes
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON inválido de {broker_key} ({port_name}): {e}")
                        except zmq.ZMQError as e:
                            if e.errno == zmq.ETERM:
                                self._running = False
                                break
                        except Exception as e:
                            logger.exception(f"Erro ao receber de {broker_key} ({port_name}): {e}")

                # Yield para o event loop - intervalo menor se havia mensagens
                await asyncio.sleep(0.01 if had_messages else 0.05)

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
