# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/zmq_message_handler.py
# Manipulador de mensagens ZMQ simplificado para copytrade.
# Removidos: signals de indicadores, OHLC, ticks, streams (não usados no copytrade).

import logging
import time
import asyncio
from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)

# Buffer global de trade_allowed por corretora
trade_allowed_states = {}


class ZmqMessageHandler(QObject):
    # Sinais mantidos (essenciais para copytrade)
    log_message_received = Signal(str)
    ping_button_state_changed = Signal(bool)
    account_balance_received = Signal(dict)
    account_flags_received = Signal(dict)
    account_margin_received = Signal(dict)
    status_info_received = Signal(dict)
    positions_received = Signal(dict)
    orders_received = Signal(dict)
    trade_allowed_update_received = Signal(dict)
    trade_event_received = Signal(dict)
    trade_response_received = Signal(dict)

    # ──────────────────────────────────────────────
    # Bloco 1 - Inicialização
    # ──────────────────────────────────────────────
    def __init__(self, config, zmq_router, broker_manager=None, copytrade_manager=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.zmq_router = zmq_router
        self.broker_manager = broker_manager
        self.copytrade_manager = copytrade_manager
        self.heartbeat_active = {}

    def set_copytrade_manager(self, copytrade_manager):
        self.copytrade_manager = copytrade_manager

    # ──────────────────────────────────────────────
    # Bloco 2 - Handler Principal
    # ──────────────────────────────────────────────
    @Slot(bytes, object)
    async def handle_zmq_message(self, client_id_bytes: bytes, message: dict):
        global trade_allowed_states

        # Identifica broker
        client_id_hex = client_id_bytes.hex()
        identified_broker_key = None
        for key, zid in self.zmq_router._clients.items():
            if zid == client_id_bytes:
                identified_broker_key = key
                break
        if not identified_broker_key:
            identified_broker_key = message.get("broker_key")

        log_prefix = f"ZMQ RX [{identified_broker_key or client_id_hex}]:"

        # Log (exceto TICK e HEARTBEAT que são muito frequentes)
        event = message.get("event")
        if event not in ("TICK", "HEARTBEAT"):
            log_message = f"{log_prefix} {message}"
            self.log_message_received.emit(log_message)
            logger.debug(log_message)

        msg_type = message.get("type")
        status = message.get("status")

        # ── SYSTEM events ──
        if msg_type == "SYSTEM" and event == "REGISTER":
            broker_key = message.get("broker_key")
            if broker_key:
                self.log_message_received.emit(f"INFO: Corretora {broker_key} registrada.")
                self.ping_button_state_changed.emit(True)
                self.heartbeat_active[broker_key] = True

        elif msg_type == "INTERNAL" and event == "CLIENT_UNREGISTERED":
            broker_key = message.get("broker_key")
            if broker_key:
                self.log_message_received.emit(f"INFO: Corretora {broker_key} desconectada.")
                self.ping_button_state_changed.emit(False)
                self.heartbeat_active.pop(broker_key, None)
                trade_allowed_states.pop(broker_key, None)

        # ── STREAM events ──
        elif msg_type == "STREAM" and event == "TRADE_ALLOWED_UPDATE":
            data = {
                "trade_allowed": message.get("trade_allowed"),
                "broker_key": identified_broker_key,
                "timestamp_mql": message.get("timestamp_mql", 0)
            }
            if identified_broker_key and data["trade_allowed"] is not None:
                trade_allowed_states[identified_broker_key] = data["trade_allowed"]
            self.trade_allowed_update_received.emit(data)

        elif msg_type == "STREAM" and event == "TRADE_EVENT":
            trade_event_data = {
                "broker_key": identified_broker_key,
                "timestamp_mql": message.get("timestamp_mql", 0),
                "request": message.get("request", {}),
                "result": message.get("", {}),
            }
            self.trade_event_received.emit(trade_event_data)
            logger.info(f"TRADE_EVENT de {identified_broker_key}")

            # Copytrade: se é do Master, replica para Slaves
            if self.copytrade_manager and self.broker_manager:
                if self.broker_manager.get_broker_role(identified_broker_key) == "master":
                    asyncio.create_task(
                        self.copytrade_manager.handle_master_trade_event(trade_event_data)
                    )

        elif msg_type == "EVENT" and event == "HEARTBEAT":
            broker_key = message.get("broker_key")
            if broker_key and broker_key not in self.heartbeat_active:
                self.heartbeat_active[broker_key] = True

        # ── RESPONSE events ──
        elif msg_type == "RESPONSE":
            request_id = message.get("request_id", "")
            self._handle_response(identified_broker_key, client_id_hex, message, request_id)

    # ──────────────────────────────────────────────
    # Bloco 3 - Handler de Respostas
    # ──────────────────────────────────────────────
    def _handle_response(self, broker_key, client_id_hex, message, request_id):
        status = message.get("status")

        if "ping_" in request_id:
            if status == "OK":
                original_ts = message.get("original_timestamp", 0)
                pong_ts_mql = message.get("pong_timestamp_mql", 0)
                current_ts = time.time()
                latency_total = (current_ts - original_ts) * 1000 if original_ts else 0
                self.log_message_received.emit(
                    f"PONG de {broker_key}! Latência: {latency_total:.1f}ms")
            else:
                self.log_message_received.emit(
                    f"ERROR: PING falhou para {broker_key}: {message.get('error_message', '?')}")

        elif "get_status_info_" in request_id:
            if status == "OK":
                original_ts = message.get("original_timestamp", 0)
                current_ts = time.time()
                latency = (current_ts - original_ts) * 1000 if original_ts else 0
                self.status_info_received.emit({
                    "trade_allowed": message.get("trade_allowed"),
                    "balance": message.get("balance"),
                    "latency": f"{latency:.1f}ms",
                    "broker_key": broker_key
                })

        elif "get_account_balance_" in request_id:
            if status == "OK":
                self.account_balance_received.emit({
                    "balance": message.get("balance"),
                    "equity": message.get("equity"),
                    "currency": message.get("currency"),
                    "broker_key": broker_key
                })

        elif "get_account_flags_" in request_id:
            if status == "OK":
                self.account_flags_received.emit({
                    "trade_allowed": message.get("trade_allowed"),
                    "expert_enabled": message.get("expert_enabled"),
                    "broker_key": broker_key
                })

        elif "get_account_margin_" in request_id:
            if status == "OK":
                self.account_margin_received.emit({
                    "margin": message.get("margin"),
                    "free_margin": message.get("free_margin"),
                    "margin_level": message.get("margin_level"),
                    "broker_key": broker_key
                })

        elif "positions_" in request_id:
            if status == "OK":
                self.positions_received.emit({
                    "data": message.get("", []),
                    "broker_key": broker_key
                })

        elif "orders_" in request_id:
            if status == "OK":
                self.orders_received.emit({
                    "orders": message.get("orders", []),
                    "broker_key": broker_key
                })

        elif any(x in request_id.lower() for x in ["trade_", "close_", "modify_", "partial_"]):
            trade_response = {
                "result": message.get("result", ""),
                "broker_key": broker_key,
                "status": status,
                "message": message.get("result", message.get("error_message", "")),
                "request_id": request_id
            }
            self.trade_response_received.emit(trade_response)
            if status != "OK":
                logger.error(f"Trade falhou para {broker_key}: {message.get('error_message', '?')}")

        else:
            if status == "OK":
                self.log_message_received.emit(f"INFO: Resposta de {broker_key}: {message}")
            else:
                self.log_message_received.emit(
                    f"ERROR: {broker_key}: {message.get('error_message', '?')}")

    # ──────────────────────────────────────────────
    # Bloco 4 - Envio de Comandos
    # ──────────────────────────────────────────────
    def send_ping(self, broker_key: str):
        timestamp = time.time()
        asyncio.create_task(self.zmq_router.send_command_to_broker(
            broker_key, "PING",
            {"timestamp": timestamp},
            f"ping_{broker_key}_{timestamp}"
        ))

    def send_get_status_info(self, broker_key: str):
        timestamp = time.time()
        asyncio.create_task(self.zmq_router.send_command_to_broker(
            broker_key, "GET_STATUS_INFO",
            {"timestamp": timestamp},
            f"get_status_info_{broker_key}_{int(timestamp)}"
        ))

    # ──────────────────────────────────────────────
    # Bloco 5 - Auxiliares
    # ──────────────────────────────────────────────
    def get_trade_allowed_states(self):
        return trade_allowed_states.copy()
