# EPCopyFlow 2.0
# core/latency_tracker.py
# Instrumentação assíncrona de latência para diagnóstico do lag durante
# Alt+Tab (issue #111). Não bloqueia o caller — toda chamada é apenas um
# `queue.put_nowait` (microssegundos). Uma thread dedicada drena a fila e
# escreve num CSV.
#
# Saída: logs/latency_<timestamp>.csv com colunas:
#   wall_time_iso, wall_time_unix, stage, broker_key, command,
#   request_id, t0_unix, delta_ms, extra
#
# Etapas medidas (correlacionar manualmente por broker_key/timestamp):
#   T1_recv_trade_event   — Python recebeu TRADE_EVENT do master (delta vs t0_mql)
#   T2_send_to_slave      — Python iniciou send_command_to_broker
#   T3_response_from_slave — Python recebeu resposta do slave (delta vs T2)
#
# Uso (singleton): set_tracker(tracker) no startup, get_tracker() nos sites.

import csv
import datetime
import logging
import os
import queue
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class LatencyTracker:
    def __init__(self, filename: Optional[str] = None):
        if filename is None:
            os.makedirs("logs", exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = os.path.join("logs", f"latency_{ts}.csv")
        self._filename = filename
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._writer_loop, name="LatencyWriter", daemon=True
        )
        self._thread.start()
        logger.info(f"LatencyTracker iniciado — gravando em {self._filename}")

    def stop(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)  # sentinela
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning("LatencyTracker writer não encerrou no timeout.")

    def _writer_loop(self) -> None:
        try:
            with open(self._filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "wall_time_iso", "wall_time_unix", "stage", "broker_key",
                    "command", "request_id", "t0_unix", "delta_ms", "extra",
                ])
                f.flush()
                while True:
                    try:
                        record = self._queue.get(timeout=0.5)
                    except queue.Empty:
                        if self._stop_event.is_set():
                            break
                        continue
                    if record is None:
                        break
                    writer.writerow(record)
                    f.flush()
        except Exception:
            logger.exception("LatencyTracker writer crashou.")

    # ── Hot-path API (chamadas devem ser baratas) ──

    def _record(self, stage: str, broker_key: str = "", command: str = "",
                request_id: str = "", t0: float = 0.0, delta_ms: Optional[float] = None,
                extra: str = "") -> None:
        now = time.time()
        iso = datetime.datetime.fromtimestamp(now).isoformat(timespec="microseconds")
        delta_str = f"{delta_ms:.2f}" if delta_ms is not None else ""
        try:
            self._queue.put_nowait([
                iso, f"{now:.6f}", stage, broker_key, command, request_id,
                f"{t0:.6f}" if t0 else "", delta_str, extra,
            ])
        except queue.Full:
            pass  # nunca bloqueia hot path

    def trade_event_received(self, broker_key: str, position_id: int,
                             timestamp_mql: float) -> float:
        """T1: Python recebeu TRADE_EVENT. Retorna o t1 (time.time()) capturado,
        para o caller correlacionar com sends subsequentes."""
        now = time.time()
        delta_ms = (now - timestamp_mql) * 1000.0 if timestamp_mql else None
        self._record(
            stage="T1_recv_trade_event",
            broker_key=broker_key,
            t0=timestamp_mql,
            delta_ms=delta_ms,
            extra=f"position_id={position_id}",
        )
        return now

    def command_sent(self, broker_key: str, command: str, request_id: str) -> float:
        """T2: prestes a enviar comando ao broker. Retorna t2 para correlacionar
        com a resposta (T3)."""
        now = time.time()
        self._record(
            stage="T2_send_to_slave",
            broker_key=broker_key,
            command=command,
            request_id=request_id,
            t0=now,
        )
        return now

    def command_response(self, broker_key: str, command: str, request_id: str,
                         t2: float, status: str = "") -> None:
        """T3: resposta do broker chegou. Loga o round-trip (T3 - T2)."""
        now = time.time()
        delta_ms = (now - t2) * 1000.0 if t2 else None
        self._record(
            stage="T3_response_from_slave",
            broker_key=broker_key,
            command=command,
            request_id=request_id,
            t0=t2,
            delta_ms=delta_ms,
            extra=f"status={status}",
        )


# ── Singleton accessors ──

_tracker: Optional[LatencyTracker] = None


def set_tracker(tracker: Optional[LatencyTracker]) -> None:
    global _tracker
    _tracker = tracker


def get_tracker() -> Optional[LatencyTracker]:
    return _tracker
