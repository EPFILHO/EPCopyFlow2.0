# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/tcp_router.py
# Roteador TCP nativo: Python é servidor, EA é cliente.
# Uma conexão TCP por broker (bidirecional), framing length-prefixed JSON.
#
# Protocolo de framing:
#   [4 bytes big-endian unsigned length][UTF-8 JSON payload]
#
# Substitui o antigo core/zmq_router.py (issue #47).
#
# PySide6.QtAsyncio não implementa create_server/add_reader, então os
# servidores TCP rodam em um event loop asyncio dedicado numa thread
# worker. Chamadas cruzam as duas loops via asyncio.run_coroutine_threadsafe
# (main → worker para envio de comandos, worker → main para entregar
# mensagens ao message_handler, que emite signals Qt).

import asyncio
import json
import logging
import struct
import threading
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
        self._clients = {}

        # Estado compartilhado entre worker loop e main loop. Mutação exclusiva no worker loop.
        self._responses = {}
        self._response_events = {}  # asyncio.Event bound ao worker loop
        self._servers = {}          # broker_key -> asyncio.base_events.Server
        self._writers = {}          # broker_key -> asyncio.StreamWriter
        self._ports = {}            # broker_key -> int

        # Sincronização entre threads
        self._main_loop = None      # asyncio loop do Qt (para entregar mensagens)
        self._worker_loop = None    # asyncio loop dedicado ao TCP
        self._worker_thread = None
        self._worker_loop_ready = threading.Event()
        self._worker_shutdown = None   # asyncio.Event criado no worker loop

        # Fila de comandos de controle (CONNECT/DISCONNECT) consumida no worker loop.
        self._socket_control_queue = None  # asyncio.Queue criada no worker loop

        logger.debug("TcpRouter inicializado (1 conexão TCP por broker, Python = server).")

    # ──────────────────────────────────────────────
    # Bloco 2 - Comandos de Controle (Connect/Disconnect)
    # ──────────────────────────────────────────────
    async def connect_broker_sockets(self, broker_key: str, broker_config: dict):
        self._submit_control(("CONNECT", broker_key, broker_config))

    async def disconnect_broker_sockets(self, broker_key: str):
        self._submit_control(("DISCONNECT", broker_key))

    def _submit_control(self, item):
        """Posta um item na fila de controle do worker loop, thread-safe."""
        if self._worker_loop is None or self._socket_control_queue is None:
            logger.warning("TcpRouter não iniciado; ignorando comando de controle.")
            return
        try:
            self._worker_loop.call_soon_threadsafe(
                self._socket_control_queue.put_nowait, item
            )
        except RuntimeError as e:
            logger.warning(f"Worker loop indisponível ao submeter {item[0]}: {e}")

    # ──────────────────────────────────────────────
    # Bloco 3 - Parada do Router
    # ──────────────────────────────────────────────
    async def stop(self):
        logger.info("Parando TcpRouter...")
        self._running = False

        if self._worker_loop is not None and self._worker_loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._worker_stop(), self._worker_loop
                )
                await asyncio.wait_for(asyncio.wrap_future(fut), timeout=3.0)
            except Exception as e:
                logger.warning(f"Erro ao parar worker loop: {e}")

        if self._worker_thread is not None:
            self._worker_thread.join(timeout=3.0)

        logger.info("TcpRouter parado.")

    async def _worker_stop(self):
        """Executado no worker loop: desliga todos os servidores e marca shutdown."""
        for event in list(self._response_events.values()):
            try:
                event.set()
            except Exception:
                pass

        for broker_key in list(self._servers.keys()):
            try:
                await self._teardown_single_broker_sockets(broker_key)
            except Exception as e:
                logger.warning(f"Erro ao teardown {broker_key} no stop: {e}")

        if self._worker_shutdown is not None:
            self._worker_shutdown.set()

    # ──────────────────────────────────────────────
    # Bloco 4 - Envio de Comandos
    # ──────────────────────────────────────────────
    async def send_command_to_broker(self, broker_key: str, command: str,
                                     payload: dict = None, request_id: str = None):
        """Envia um comando ao EA via TCP e aguarda resposta.

        Chamada no main loop (Qt). Agenda a execução real no worker loop."""
        if not broker_key:
            return {"status": "ERROR", "message": "broker_key não fornecida."}
        if self._worker_loop is None:
            return {"status": "ERROR", "message": "Router não iniciado."}

        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._send_command_internal(broker_key, command, payload, request_id),
                self._worker_loop,
            )
            return await asyncio.wrap_future(fut)
        except Exception as e:
            logger.error(f"Erro ao marshallar {command} para {broker_key}: {e}")
            return {"status": "ERROR", "message": str(e)}

    async def _send_command_internal(self, broker_key: str, command: str,
                                     payload: dict, request_id: str):
        """Executado no worker loop: escreve o frame e aguarda resposta."""
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

    # ──────────────────────────────────────────────
    # Bloco 6 - Processamento de Mensagens (fast-path síncrono)
    # ──────────────────────────────────────────────
    def _process_message(self, message_data: dict, broker_key: str):
        """Fast-path síncrono (worker loop): sinaliza respostas e despacha ao main loop."""
        msg_type = message_data.get("type")
        event = message_data.get("event")
        broker_key_msg = message_data.get("broker_key")
        request_id = message_data.get("request_id")

        if msg_type == "RESPONSE" and request_id:
            self._responses[request_id] = message_data
            ev = self._response_events.get(request_id)
            if ev is not None:
                ev.set()

        if msg_type == "SYSTEM" and event == "REGISTER" and broker_key_msg:
            self._clients[broker_key_msg] = broker_key
        elif msg_type == "SYSTEM" and event == "UNREGISTER" and broker_key_msg:
            self._clients.pop(broker_key_msg, None)

        # Despacha o processamento pesado para o main loop (onde vivem os signals Qt).
        if self._message_handler and self._main_loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._dispatch_to_handler(broker_key, message_data),
                    self._main_loop,
                )
            except RuntimeError as e:
                logger.warning(f"Main loop indisponível ao dispatchar: {e}")

    async def _dispatch_to_handler(self, broker_key: str, message_data: dict):
        """Executado no main loop (Qt). Chama o message_handler."""
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
    # Bloco 7 - Server/Client connection handling (worker loop)
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
                if self._writers.get(broker_key) is writer:
                    self._writers.pop(broker_key, None)
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
    # Bloco 8 - Worker loop e bootstrap
    # ──────────────────────────────────────────────
    def _worker_thread_main(self):
        """Thread dedicada: roda um event loop asyncio próprio para TCP."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._worker_loop = loop
        try:
            loop.run_until_complete(self._worker_main())
        except Exception as e:
            logger.exception(f"Worker loop encerrou com erro: {e}")
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            logger.info("Worker loop TcpRouter finalizado.")

    async def _worker_main(self):
        """Rodando no worker loop. Cria fila + shutdown event e processa controle."""
        self._socket_control_queue = asyncio.Queue()
        self._worker_shutdown = asyncio.Event()
        self._worker_loop_ready.set()
        logger.info("Loop de controle TcpRouter iniciado.")

        try:
            while self._running and not self._worker_shutdown.is_set():
                # Aguarda um item da fila OU o shutdown event, o que vier primeiro.
                get_task = asyncio.ensure_future(self._socket_control_queue.get())
                shutdown_task = asyncio.ensure_future(self._worker_shutdown.wait())
                done, pending = await asyncio.wait(
                    {get_task, shutdown_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()

                if shutdown_task in done:
                    break

                try:
                    cmd = get_task.result()
                except Exception:
                    continue

                cmd_type, broker_key, *args = cmd
                try:
                    if cmd_type == "CONNECT":
                        await self._setup_single_broker_sockets(broker_key, args[0])
                    elif cmd_type == "DISCONNECT":
                        await self._teardown_single_broker_sockets(broker_key)
                except Exception as e:
                    logger.exception(f"Erro processando {cmd_type} de {broker_key}: {e}")
                finally:
                    try:
                        self._socket_control_queue.task_done()
                    except Exception:
                        pass
        finally:
            logger.info("Loop de controle TcpRouter finalizado.")

    async def run(self, message_handler):
        """Chamado no main loop (Qt). Inicia a thread worker e mantém a task viva."""
        logger.info("TcpRouter.run() iniciado.")
        self._message_handler = message_handler
        self._main_loop = asyncio.get_running_loop()
        self._running = True
        self._worker_loop_ready.clear()

        self._worker_thread = threading.Thread(
            target=self._worker_thread_main,
            name="TcpRouterWorker",
            daemon=True,
        )
        self._worker_thread.start()

        # Aguarda o worker loop sinalizar que está pronto
        for _ in range(50):
            if self._worker_loop_ready.is_set():
                break
            await asyncio.sleep(0.05)

        if not self._worker_loop_ready.is_set():
            logger.error("Worker loop do TcpRouter não inicializou a tempo.")
            return

        try:
            while self._running:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            self._running = False
        finally:
            logger.info("TcpRouter.run() finalizado.")
