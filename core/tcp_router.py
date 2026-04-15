# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/tcp_router.py
# Roteador TCP nativo: Python é servidor, EA é cliente.
# Uma conexão TCP por broker (bidirecional), framing length-prefixed JSON.
#
# Protocolo de framing:
#   [4 bytes big-endian unsigned length][UTF-8 JSON payload]
#
# Substitui o antigo core/zmq_router.py (migrado para TCP nativo no issue #47).
#
# Transporte: socket bloqueante + threading puro (sem asyncio nas threads worker).
# Isso evita problemas com IocpProactor do Windows em threads não-principais.
# O lado asyncio (main loop Qt) só envolve: run(), stop(), send_command_to_broker(),
# configure_*() e o dispatcher de mensagens de entrada para os signals Qt.

import asyncio
import json
import logging
import queue
import socket
import struct
import threading
import time

logger = logging.getLogger(__name__)

# Tamanho máximo de frame aceito (sanity check): 16 MiB.
_MAX_FRAME_SIZE = 16 * 1024 * 1024

# Timeout de socket de cliente (para recv bloqueante poder verificar self._running)
_CONN_RECV_TIMEOUT = 1.0


class TcpRouter:
    # ──────────────────────────────────────────────
    # Bloco 1 - Inicialização
    # ──────────────────────────────────────────────
    def __init__(self, broker_manager):
        self.broker_manager = broker_manager
        self._running = False
        self._message_handler = None
        self._main_loop = None

        # Mapa legado usado pelo message_handler para resolver broker_key por id de cliente.
        self._clients = {}  # broker_key -> broker_key

        # Sockets de servidor (broker_key -> socket bloqueante)
        self._server_sockets = {}
        # Sockets de conexão ativa com o EA (broker_key -> socket)
        self._conn_sockets = {}
        # Locks de envio por conexão
        self._conn_locks = {}

        # Futures para respostas de comandos (request_id -> asyncio.Future no main loop)
        self._response_futures = {}

        # Fila de comandos de controle (CONNECT/DISCONNECT) para a thread de controle.
        self._control_queue = queue.Queue()
        self._control_thread = None

        # Rastreamento de threads worker (accept + read) para shutdown gracioso.
        # Ao parar, damos join em todas antes de liberar o main loop, garantindo
        # que nenhuma chamada a _process_message fica "em voo" após stop() retornar.
        self._worker_threads = []
        self._worker_threads_lock = threading.Lock()

        # Refs fortes aos tasks de dispatch criados via call_soon_threadsafe.
        # Evita GC prematuro (conforme docs do asyncio) e permite limpeza no stop.
        self._pending_dispatch_tasks = set()

        logger.debug("TcpRouter inicializado (socket bloqueante + threading, Python = server).")

    def _spawn_worker(self, target, args, name):
        """Cria e inicia uma thread worker, rastreando-a para o shutdown."""
        t = threading.Thread(target=target, args=args, daemon=True, name=name)
        with self._worker_threads_lock:
            # Limpa entradas já finalizadas para não inchar a lista ao longo do tempo
            self._worker_threads = [w for w in self._worker_threads if w.is_alive()]
            self._worker_threads.append(t)
        t.start()
        return t

    # ──────────────────────────────────────────────
    # Bloco 2 - Comandos de Controle (Connect/Disconnect)
    # ──────────────────────────────────────────────
    async def connect_broker_sockets(self, broker_key: str, broker_config: dict):
        self._control_queue.put(("CONNECT", broker_key, broker_config))

    async def disconnect_broker_sockets(self, broker_key: str):
        self._control_queue.put(("DISCONNECT", broker_key))

    def _control_loop(self):
        """Thread de controle: processa CONNECT/DISCONNECT da fila."""
        logger.info("Thread de controle TcpRouter iniciada.")
        while self._running:
            try:
                cmd = self._control_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            cmd_type = cmd[0]
            broker_key = cmd[1]
            try:
                if cmd_type == "CONNECT":
                    self._start_server(broker_key, cmd[2])
                elif cmd_type == "DISCONNECT":
                    self._stop_server(broker_key)
            except Exception as e:
                logger.exception(f"Erro de controle para {broker_key}: {e}")

        logger.info("Thread de controle TcpRouter encerrada.")

    # ──────────────────────────────────────────────
    # Bloco 3 - Gerenciamento de Servidor TCP
    # ──────────────────────────────────────────────
    def _start_server(self, broker_key: str, config: dict):
        port = config.get('command_port')
        if not port:
            logger.warning(f"command_port não definida para {broker_key}")
            return
        port = int(port)

        self._stop_server(broker_key)

        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(('127.0.0.1', port))
            srv.listen(1)
            srv.settimeout(1.0)   # timeout no accept() para verificar self._running
        except OSError as e:
            logger.error(f"Falha ao escutar porta {port} para {broker_key}: {e}")
            return

        self._server_sockets[broker_key] = srv
        logger.info(f"TCP server escutando em 127.0.0.1:{port} para {broker_key}")

        self._spawn_worker(
            target=self._accept_loop,
            args=(broker_key, srv),
            name=f"TcpAccept-{broker_key}",
        )

    def _stop_server(self, broker_key: str):
        conn = self._conn_sockets.pop(broker_key, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

        srv = self._server_sockets.pop(broker_key, None)
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass

        self._conn_locks.pop(broker_key, None)
        self._clients.pop(broker_key, None)
        logger.info(f"TCP server parado para {broker_key}")

    # ──────────────────────────────────────────────
    # Bloco 4 - Accept Loop e Read Loop
    # ──────────────────────────────────────────────
    def _accept_loop(self, broker_key: str, srv: socket.socket):
        while self._running and broker_key in self._server_sockets:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            logger.info(f"[{broker_key}] EA conectado de {addr}")

            old_conn = self._conn_sockets.pop(broker_key, None)
            if old_conn is not None:
                logger.warning(f"[{broker_key}] Substituindo conexão existente.")
                try:
                    old_conn.close()
                except Exception:
                    pass

            conn.settimeout(_CONN_RECV_TIMEOUT)
            self._conn_sockets[broker_key] = conn
            self._conn_locks[broker_key] = threading.Lock()
            self._clients[broker_key] = broker_key

            self._spawn_worker(
                target=self._read_loop,
                args=(broker_key, conn),
                name=f"TcpRead-{broker_key}",
            )

    def _read_loop(self, broker_key: str, conn: socket.socket):
        try:
            while self._running:
                header = self._recv_exact(conn, 4)
                if header is None:
                    logger.info(f"[{broker_key}] EA desconectou (EOF/timeout).")
                    break

                (length,) = struct.unpack(">I", header)
                if length == 0:
                    continue
                if length > _MAX_FRAME_SIZE:
                    logger.error(f"[{broker_key}] Frame muito grande: {length}. Encerrando.")
                    break

                payload = self._recv_exact(conn, length)
                if payload is None:
                    logger.warning(f"[{broker_key}] EOF no meio de frame.")
                    break

                try:
                    data = json.loads(payload.decode('utf-8', errors='replace'))
                except json.JSONDecodeError as e:
                    logger.error(f"[{broker_key}] JSON inválido: {e}")
                    continue

                self._process_message(data, broker_key)

        except Exception as e:
            logger.exception(f"[{broker_key}] Erro no read loop: {e}")
        finally:
            if self._conn_sockets.get(broker_key) is conn:
                self._conn_sockets.pop(broker_key, None)
                self._clients.pop(broker_key, None)
                try:
                    self._process_message(
                        {"type": "SYSTEM", "event": "UNREGISTER", "broker_key": broker_key},
                        broker_key,
                    )
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass
            logger.info(f"[{broker_key}] Read loop encerrado.")

    def _recv_exact(self, conn: socket.socket, n: int):
        """Lê exatamente n bytes do socket. Retorna None em EOF, erro ou shutdown."""
        data = b""
        while len(data) < n:
            try:
                chunk = conn.recv(n - len(data))
                if not chunk:
                    return None   # EOF
                data += chunk
            except socket.timeout:
                if not self._running:
                    return None   # Shutdown solicitado
                # Timeout normal: continua esperando dados
                continue
            except Exception:
                return None
        return data

    # ──────────────────────────────────────────────
    # Bloco 5 - Processamento de Mensagens
    # ──────────────────────────────────────────────
    def _process_message(self, message_data: dict, broker_key: str):
        """Chamado da thread de leitura. Resolve futures de resposta e despacha ao main loop."""
        msg_type = message_data.get("type")
        event = message_data.get("event")
        request_id = message_data.get("request_id")

        # Resolver futures de resposta (via call_soon_threadsafe para ser thread-safe)
        if msg_type == "RESPONSE" and request_id and self._main_loop:
            future = self._response_futures.get(request_id)
            if future is not None:
                def _set(f=future, d=message_data):
                    if not f.done():
                        f.set_result(d)
                try:
                    self._main_loop.call_soon_threadsafe(_set)
                except RuntimeError:
                    pass

        # Atualizar mapa legado de clientes
        if msg_type == "SYSTEM" and event == "REGISTER":
            broker_key_msg = message_data.get("broker_key")
            if broker_key_msg:
                self._clients[broker_key_msg] = broker_key

        # Despachar ao main loop (onde vivem os signals Qt).
        #
        # IMPORTANTE: NÃO criar o coroutine aqui na worker thread e passar
        # para run_coroutine_threadsafe. Durante o shutdown existe uma janela
        # onde call_soon_threadsafe aceita o callback (loop ainda "running"),
        # mas o loop fecha antes do callback executar ensure_future(coro) —
        # o coroutine é silenciosamente descartado, nunca é iterado, e o
        # Python emite "RuntimeWarning: coroutine was never awaited" no GC
        # final do interpretador. coro.close() no except RuntimeError NÃO
        # resolve porque call_soon_threadsafe não levanta nesse cenário.
        #
        # Fix: passar um callable síncrono leve. O coroutine só é criado
        # dentro do loop, já envolvido por um Task (iterado pelo menos uma
        # vez) — então nunca há coroutine "no ar" entre threads.
        if self._message_handler and self._main_loop and self._running:
            main_loop = self._main_loop

            def _schedule_dispatch():
                if not self._running:
                    return
                try:
                    task = main_loop.create_task(
                        self._dispatch_to_handler(broker_key, message_data)
                    )
                except RuntimeError:
                    # Loop já fechado — nada a fazer.
                    return
                self._pending_dispatch_tasks.add(task)
                task.add_done_callback(self._pending_dispatch_tasks.discard)

            try:
                main_loop.call_soon_threadsafe(_schedule_dispatch)
            except RuntimeError as e:
                # Loop já fechado antes do agendamento — callback nem chega
                # a ser enfileirado. Nenhum coroutine foi criado. Sem warning.
                logger.debug(f"Main loop indisponível ao dispatchar (shutdown): {e}")

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
                    "tcp_id": broker_key,
                }
                await self._message_handler.handle_tcp_message(
                    broker_key.encode('utf-8'), notification)
            else:
                await self._message_handler.handle_tcp_message(
                    broker_key.encode('utf-8'), message_data)
        except Exception as e:
            logger.exception(f"Erro ao despachar mensagem de {broker_key}: {e}")

    # ──────────────────────────────────────────────
    # Bloco 6 - Envio de Comandos
    # ──────────────────────────────────────────────
    async def send_command_to_broker(self, broker_key: str, command: str,
                                     payload: dict = None, request_id: str = None):
        """Envia um comando ao EA e aguarda resposta (async, no main loop)."""
        if not broker_key:
            return {"status": "ERROR", "message": "broker_key não fornecida."}

        conn = self._conn_sockets.get(broker_key)
        if conn is None:
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

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._response_futures[request_id] = future

        try:
            frame = self._encode_frame(message)
            lock = self._conn_locks.get(broker_key, threading.Lock())

            def do_send():
                with lock:
                    conn.sendall(frame)

            try:
                await loop.run_in_executor(None, do_send)
            except Exception as e:
                logger.warning(f"Falha ao enviar {command} para {broker_key}: {e}")
                return {"status": "ERROR", "message": f"Falha no envio: {e}"}

            logger.debug(f"Comando {command} enviado para {broker_key} (id={request_id})")

            try:
                return await asyncio.wait_for(future, timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"Timeout aguardando resposta de {command} de {broker_key}")
                return {"status": "ERROR", "message": "Timeout na resposta"}

        finally:
            self._response_futures.pop(request_id, None)

    async def configure_magic_number(self, broker_key: str):
        """Envia SET_MAGIC_NUMBER ao EA lendo o valor do config.ini principal.
        Chamado uma única vez logo após receber REGISTER do EA. O Python é a fonte
        única da verdade do magic number — o EA não lê mais do config.ini por instância.
        """
        try:
            import configparser
            config = configparser.ConfigParser()
            config.read("config.ini")
            magic = int(config.get("CopyTrade", "magic_number", fallback="0"))

            response = await self.send_command_to_broker(
                broker_key,
                "SET_MAGIC_NUMBER",
                {"magic_number": magic},
                f"set_magic_{broker_key}_{int(time.time())}",
            )
            if response.get("status") == "OK":
                logger.info(f"Magic number configurado em {broker_key}: {magic}")
            else:
                logger.warning(
                    f"Falha ao configurar magic em {broker_key}: "
                    f"{response.get('error_message', '?')}"
                )
        except Exception as e:
            logger.error(f"Erro ao configurar magic em {broker_key}: {e}", exc_info=True)

    # ──────────────────────────────────────────────
    # Bloco 7 - Inicialização e Parada
    # ──────────────────────────────────────────────
    async def run(self, message_handler):
        """Chamado no main loop (Qt). Inicia a thread de controle e mantém a task viva."""
        logger.info("TcpRouter.run() iniciado.")
        self._message_handler = message_handler
        self._main_loop = asyncio.get_running_loop()
        self._running = True

        self._control_thread = threading.Thread(
            target=self._control_loop,
            daemon=True,
            name="TcpRouterControl",
        )
        self._control_thread.start()

        try:
            while self._running:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            self._running = False
        finally:
            logger.info("TcpRouter.run() finalizado.")

    async def stop(self):
        logger.info("Parando TcpRouter...")
        self._running = False

        for broker_key in list(self._server_sockets.keys()):
            self._stop_server(broker_key)

        if self._control_thread is not None:
            self._control_thread.join(timeout=3.0)

        # Aguarda todas as threads de accept/read terminarem ANTES de retornar.
        # Isso garante que nenhuma chamada a _process_message fica em voo após
        # este ponto — o main loop pode fechar sem coroutines órfãs.
        with self._worker_threads_lock:
            workers = [w for w in self._worker_threads if w.is_alive()]
            self._worker_threads.clear()
        for w in workers:
            w.join(timeout=2.0)
            if w.is_alive():
                logger.warning(f"Worker thread {w.name} não encerrou em 2s.")

        # Cancela tasks de dispatch ainda pendentes (se houver). Estamos no
        # main loop aqui, então create_task já foi iterado pelo scheduler —
        # não há risco de "never awaited". Cancelar é só cortesia para
        # acelerar o encerramento limpo.
        pending = [t for t in self._pending_dispatch_tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                pass
        self._pending_dispatch_tasks.clear()

        logger.info("TcpRouter parado.")

    # ──────────────────────────────────────────────
    # Bloco 8 - Framing
    # ──────────────────────────────────────────────
    @staticmethod
    def _encode_frame(message: dict) -> bytes:
        payload = json.dumps(message, ensure_ascii=False).encode('utf-8')
        if len(payload) > _MAX_FRAME_SIZE:
            raise ValueError(f"Frame excede tamanho máximo ({len(payload)} > {_MAX_FRAME_SIZE})")
        return struct.pack(">I", len(payload)) + payload
