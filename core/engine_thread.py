# EPCopyFlow 2.0
# core/engine_thread.py
# Thread dedicada que hospeda o event loop do motor de trade (asyncio),
# isolando-o do event loop da GUI (Qt main thread). Ver issue #111.
#
# Uso:
#     engine = EngineThread(name="AsyncEngine")
#     engine.start()                          # bloqueia até loop estar pronto
#     fut = engine.submit(some_coroutine())   # concurrent.futures.Future
#     engine.stop(timeout=5.0)
#
# Esta classe é deliberadamente neutra: não conhece Qt, TcpRouter,
# CopyTradeManager ou qualquer outro componente do app. O bootstrap
# do motor (construir QObjects DENTRO desta thread) é responsabilidade
# do main.py, via `engine.submit(bootstrap_coro())`.

import asyncio
import concurrent.futures
import logging
import threading
from typing import Coroutine, Optional

logger = logging.getLogger(__name__)


class EngineThread:
    """
    Hospeda um event loop asyncio em uma thread daemon dedicada.

    Garantias:
    - `start()` só retorna quando o loop está rodando e pronto para receber `submit`.
    - `submit(coro)` é thread-safe e retorna um `concurrent.futures.Future`.
    - Exceções dentro de coroutines submetidas NÃO derrubam o loop;
      ficam disponíveis no Future retornado por `submit`.
    - `stop()` cancela tasks pendentes, para o loop e faz join da thread.
    """

    def __init__(self, name: str = "AsyncEngine", ready_timeout: float = 5.0):
        self._name = name
        self._ready_timeout = ready_timeout
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stopped = False
        self._lock = threading.Lock()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """Event loop hospedado pela thread. Disponível após `start()`."""
        if self._loop is None:
            raise RuntimeError("EngineThread.start() ainda não foi chamado.")
        return self._loop

    @property
    def is_running(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self._loop is not None
            and self._loop.is_running()
        )

    def start(self) -> None:
        """
        Inicia a thread e o event loop. Bloqueia até o loop estar pronto.
        Chamadas múltiplas são idempotentes (no-op se já iniciado).
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.warning("EngineThread já está rodando — start() ignorado.")
                return
            if self._stopped:
                raise RuntimeError("EngineThread já foi parado e não pode ser reiniciado.")

            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run, name=self._name, daemon=True
            )
            self._thread.start()

        if not self._ready.wait(timeout=self._ready_timeout):
            raise RuntimeError(
                f"EngineThread não ficou pronto em {self._ready_timeout}s."
            )
        logger.info(f"EngineThread '{self._name}' iniciada e pronta.")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(self._loop_exception_handler)
        try:
            loop.call_soon(self._ready.set)
            loop.run_forever()
        finally:
            try:
                self._drain(loop)
            finally:
                asyncio.set_event_loop(None)
                loop.close()
                logger.info(f"EngineThread '{self._name}' loop encerrado.")

    @staticmethod
    def _loop_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        # Loga e segue: uma exceção em callback/Future não derruba o loop.
        msg = context.get("message") or "unhandled exception in engine loop"
        exc = context.get("exception")
        if exc is not None:
            logger.error(f"[EngineThread] {msg}", exc_info=exc)
        else:
            logger.error(f"[EngineThread] {msg} | context={context}")

    @staticmethod
    def _drain(loop: asyncio.AbstractEventLoop) -> None:
        """Cancela tasks pendentes e roda o loop até elas finalizarem."""
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if not pending:
            return
        logger.info(f"[EngineThread] cancelando {len(pending)} task(s) pendente(s).")
        for task in pending:
            task.cancel()
        try:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        except Exception:
            logger.exception("[EngineThread] erro ao drenar tasks pendentes.")

    def submit(self, coro: Coroutine) -> concurrent.futures.Future:
        """
        Agenda `coro` no loop do motor a partir de qualquer thread.
        Retorna um `concurrent.futures.Future` com o resultado/exceção.
        """
        if not asyncio.iscoroutine(coro):
            raise TypeError(
                f"submit() espera uma coroutine, recebeu {type(coro).__name__}."
            )
        if self._loop is None or not self._loop.is_running():
            # Fecha a coroutine para evitar warning "never awaited".
            coro.close()
            raise RuntimeError("EngineThread não está rodando — não é possível submit().")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self, timeout: float = 5.0) -> bool:
        """
        Para o loop ordenadamente: cancela tasks pendentes, para o loop e
        faz join da thread. Retorna True se a thread terminou dentro do timeout.
        Idempotente.
        """
        with self._lock:
            if self._stopped:
                return True
            if self._thread is None:
                self._stopped = True
                return True
            self._stopped = True

        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

        self._thread.join(timeout=timeout)
        alive = self._thread.is_alive()
        if alive:
            logger.warning(
                f"EngineThread '{self._name}' não terminou em {timeout}s "
                f"(thread ainda viva — possível coroutine travada)."
            )
        return not alive
