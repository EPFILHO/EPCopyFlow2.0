# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/tcp_router.py
# Roteador TCP nativo: Python é servidor, EA é cliente.
# Uma conexão TCP por broker (bidirecional), framing length-prefixed JSON.
#
# Protocolo de framing:
#   [4 bytes big-endian unsigned length][UTF-8 JSON payload]
#
# Substitui o antigo core/zmq_router.py (issue #47).

import asyncio
import json
import logging
import struct
import time

logger = logging.getLogger(__name__)

# Tamanho máximo de frame aceito (sanity check): 16 MiB.
_MAX_FRAME_SIZE = 16 * 1024 * 1024


class TcpRouter:
    # ──────────────────────────────────────────────
    # Bloco 1 - Inicialização
    # ──────────────────────────────────────────────
    def __init__(self, broker_manager):
        self.broker_manager = broker_manager
        self._running = False
        self._message_handler = None

        # Mapa legado usado pelo message_handler para resolver broker_key por id de cliente.
        # Mantido por compatibilidade; cada broker só tem um cliente, então mapeamos
        # broker_key -> broker_key (o "zmq id" virou o próprio broker_key).
        self._clients = {}

        self._responses = {}
        self._response_events = {}

        # Por-broker:
        #   self._servers[broker_key]     -> asyncio.base_events.Server
        #   self._writers[broker_key]     -> asyncio.StreamWriter (conexão ativa do EA)
        #   self._reader_tasks[broker_key]-> asyncio.Task do loop de leitura
        #   self._ports[broker_key]       -> int (porta TCP escutada)
        self._servers = {}
        self._writers = {}
        self._reader_tasks = {}
        self._ports = {}

        self._socket_control_queue = asyncio.Queue()
        self._background_tasks: set = set()
        logger.debug("TcpRouter inicializado (1 conexão TCP por broker, Python = server).")

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
        logger.info("Parando TcpRouter...")
        self._running = False

        for event in self._response_events.values():
            event.set()

        all_broker_keys = set(self._servers.keys()) | set(self._writers.keys())
        for broker_key in all_broker_keys:
            await self._socket_control_queue.put(("DISCONNECT", broker_key))

        try:
            await asyncio.wait_for(self._socket_control_queue.join(), timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass

        # Fallback: desliga o que sobrou.
        for broker_key in list(self._servers.keys()):
            await self._teardown_single_broker_sockets(broker_key)

        logger.info("TcpRouter parado.")

    # ──────────────────────────────────────────────
    # Bloco 4 - Envio de Comandos
    # ──────────────────────────────────────────────
    async def send_command_to_broker(self, broker_key: str, command: str,
                                     payload: dict = None, request_id: str = None):
        """Envia um comando ao EA via TCP e aguarda resposta."""
        if not broker_key:
            return {"status": "ERROR", "message": "broker_key não fornecida."}

        writer = self._writers.get(broker_key)
        if writer is None or writer.is_closing():
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
            frame = self._encode_frame(message)
            writer.write(frame)
            try:
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError) as e:
                logger.warning(f"Conexão caiu ao enviar {command} para {broker_key}: {e}")
                return {"status": "ERROR", "message": f"Conexão perdida: {e}"}

            logger.debug(f"Comando {command} enviado para {broker_key} (id={request_id})")
            await asyncio.wait_for(self._response_events[request_id].wait(), timeout=5.0)
            if request_id in self._responses:
                return self._responses.pop(request_id)
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
        """Lê do config.ini e envia SET_HEARTBEAT_INTERVAL."""
        try:
            import configparser
            config = configparser.ConfigParser()
            config.read("config.ini")

            interval_seconds = int(config.get("CopyTrade", "heartbeat_interval", fallback="5"))
            interval_ms = interval_seconds * 1000

            if interval_ms < 1000 or interval_ms > 600000:
                interval_ms = 5000
                logger.warning(f"Intervalo inválido, usando default: {interval_ms}ms")

            response = await self.send_command_to_broker(
                broker_key,
                "SET_HEARTBEAT_INTERVAL",
                {"heartbeat_interval_ms": interval_ms},
                f"set_heartbeat_{broker_key}_{int(time.time())}"
            )

            if response.get("status") == "OK":
                logger.info(f"Heartbeat configurado em {broker_key}: {interval_ms}ms")
            else:
                logger.warning(f"Falha ao configurar heartbeat em {broker_key}: {response.get('error_message', '?')}")

        except Exception as e:
            logger.error(f"Erro ao configurar heartbeat em {broker_key}: {e}", exc_info=True)

    async def configure_magic_number(self, broker_key: str):
        """Lê do config.ini e envia SET_MAGIC_NUMBER."""
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
    # Bloco 5 - Framing helpers
    # ──────────────────────────────────────────────
    @staticmethod
    def _encode_frame(message: dict) -> bytes:
        payload = json.dumps(message, ensure_ascii=False).encode('utf-8')
        if len(payload) > _MAX_FRAME_SIZE:
            raise ValueError(f"Frame excede tamanho máximo ({len(payload)} > {_MAX_FRAME_SIZE})")
        return struct.pack(">I", len(payload)) + payload

    @staticmethod
    async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
        """Lê exatamente n bytes do reader. Lança IncompleteReadError se EOF."""
        return await reader.readexactly(n)

    # ──────────────────────────────────────────────
    # Bloco 6 - Processamento de Mensagens (fast-path síncrono)
    # ──────────────────────────────────────────────
    def _process_message(self, message_data: dict, broker_key: str):
        """Fast-path síncrono: sinaliza respostas e atualiza estado."""
        msg_type = message_data.get("type")
        event = message_data.get("event")
        broker_key_msg = message_data.get("broker_key")
        request_id = message_data.get("request_id")

        if msg_type == "RESPONSE" and request_id:
            self._responses[request_id] = message_data
            if request_id in self._response_events:
                self._response_events[request_id].set()

        if msg_type == "SYSTEM" and event == "REGISTER" and broker_key_msg:
            self._clients[broker_key_msg] = broker_key
        elif msg_type == "SYSTEM" and event == "UNREGISTER" and broker_key_msg:
            self._clients.pop(broker_key_msg, None)

        if self._message_handler:
            task = asyncio.create_task(self._dispatch_message(broker_key, message_data))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

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
    # Bloco 7 - Server/Client connection handling
    # ──────────────────────────────────────────────
    def _make_client_handler(self, broker_key: str):
        """Cria um handler coroutine para asyncio.start_server vinculado ao broker."""
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            peer = writer.get_extra_info('peername')
            logger.info(f"[{broker_key}] EA conectado de {peer}")

            # Se já havia um writer para este broker, fechamos o antigo.
            old_writer = self._writers.get(broker_key)
            if old_writer is not None and not old_writer.is_closing():
                logger.warning(f"[{broker_key}] Substituindo conexão existente do EA.")
                try:
                    old_writer.close()
                except Exception:
                    pass

            self._writers[broker_key] = writer
            # Registra mapa legado usado pelo message_handler
            self._clients[broker_key] = broker_key

            try:
                while self._running:
                    try:
                        header = await reader.readexactly(4)
                    except asyncio.IncompleteReadError:
                        logger.info(f"[{broker_key}] EA desconectou (EOF).")
                        break
                    (length,) = struct.unpack(">I", header)
                    if length == 0:
                        continue
                    if length > _MAX_FRAME_SIZE:
                        logger.error(f"[{broker_key}] Frame excede limite ({length}), fechando.")
                        break
                    try:
                        payload = await reader.readexactly(length)
                    except asyncio.IncompleteReadError:
                        logger.warning(f"[{broker_key}] EOF no meio de frame.")
                        break

                    try:
                        data = json.loads(payload.decode('utf-8', errors='replace'))
                    except json.JSONDecodeError as e:
                        logger.error(f"[{broker_key}] JSON inválido: {e}")
                        continue

                    self._process_message(data, broker_key)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"[{broker_key}] Erro no handler de cliente: {e}")
            finally:
                # Limpa o writer se ainda é o atual
                if self._writers.get(broker_key) is writer:
                    self._writers.pop(broker_key, None)
                    # Notifica unregister implícito
                    unregister_msg = {
                        "type": "SYSTEM",
                        "event": "UNREGISTER",
                        "broker_key": broker_key,
                    }
                    try:
                        self._process_message(unregister_msg, broker_key)
                    except Exception:
                        pass
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                logger.info(f"[{broker_key}] Handler de cliente finalizado.")

        return handler

    async def _setup_single_broker_sockets(self, broker_key: str, config: dict):
        logger.info(f"Configurando servidor TCP para {broker_key}...")

        # Limpa estado residual
        self._clients.pop(broker_key, None)
        await self._teardown_single_broker_sockets(broker_key)

        command_port = config.get('command_port')
        if not command_port:
            logger.warning(f"command_port não definida para {broker_key}")
            return

        try:
            server = await asyncio.start_server(
                self._make_client_handler(broker_key),
                host='127.0.0.1',
                port=int(command_port),
                reuse_address=True,
            )
        except OSError as e:
            logger.error(f"Falha ao escutar porta {command_port} para {broker_key}: {e}")
            return

        self._servers[broker_key] = server
        self._ports[broker_key] = int(command_port)
        logger.info(f"Servidor TCP escutando em 127.0.0.1:{command_port} para {broker_key}")

    async def _teardown_single_broker_sockets(self, broker_key: str):
        logger.info(f"Fechando servidor/conexão TCP para {broker_key}...")

        writer = self._writers.pop(broker_key, None)
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except Exception:
                pass

        server = self._servers.pop(broker_key, None)
        if server is not None:
            try:
                server.close()
                await server.wait_closed()
            except Exception as e:
                logger.warning(f"Erro ao fechar servidor de {broker_key}: {e}")

        self._ports.pop(broker_key, None)
        self._clients.pop(broker_key, None)

    # ──────────────────────────────────────────────
    # Bloco 8 - Loop Principal (processa fila de controle)
    # ──────────────────────────────────────────────
    async def _control_loop(self):
        """Processa comandos CONNECT/DISCONNECT da fila. Servidores TCP rodam sozinhos."""
        logger.info("Loop de controle TcpRouter iniciado.")
        self._running = True

        while self._running:
            try:
                try:
                    cmd = await asyncio.wait_for(
                        self._socket_control_queue.get(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    continue

                cmd_type, broker_key, *args = cmd
                try:
                    if cmd_type == "CONNECT":
                        await self._setup_single_broker_sockets(broker_key, args[0])
                    elif cmd_type == "DISCONNECT":
                        await self._teardown_single_broker_sockets(broker_key)
                finally:
                    self._socket_control_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Erro no loop _control_loop: {e}")
                await asyncio.sleep(0.5)

        logger.info("Loop de controle TcpRouter finalizado.")

    async def run(self, message_handler):
        logger.info("TcpRouter.run() iniciado.")
        self._message_handler = message_handler
        self._running = True
        try:
            await self._control_loop()
        except asyncio.CancelledError:
            self._running = False
        except Exception as e:
            logger.exception(f"Erro crítico em TcpRouter.run(): {e}")
            self._running = False
        finally:
            logger.info("TcpRouter.run() finalizado.")
