# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/tcp_message_handler.py
# Manipulador de mensagens TCP simplificado para copytrade.
# Removidos: signals de indicadores, OHLC, ticks, streams (não usados no copytrade).

import logging
import time
import asyncio
from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)

# Buffers globais de estado por corretora
trade_allowed_states = {}
connection_status_states = {}  # True = broker conectado ao servidor, False = desconectado


class TcpMessageHandler(QObject):
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
    connection_status_received = Signal(dict)
    trade_event_received = Signal(dict)
    trade_response_received = Signal(dict)
    alien_trade_detected = Signal(dict)

    # ──────────────────────────────────────────────
    # Bloco 1 - Inicialização
    # ──────────────────────────────────────────────
    def __init__(self, config, tcp_router, broker_manager=None, copytrade_manager=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.tcp_router = tcp_router
        self.broker_manager = broker_manager
        self.copytrade_manager = copytrade_manager
        self.heartbeat_active = {}
        self._background_tasks: set = set()

    def set_copytrade_manager(self, copytrade_manager):
        self.copytrade_manager = copytrade_manager

    # ──────────────────────────────────────────────
    # Bloco 2 - Handler Principal
    # ──────────────────────────────────────────────
    @Slot(bytes, object)
    async def handle_tcp_message(self, client_id_bytes: bytes, message: dict):
        global trade_allowed_states, connection_status_states

        # Identifica broker
        client_id_hex = client_id_bytes.hex()
        identified_broker_key = None
        for key, zid in self.tcp_router._clients.items():
            if zid == client_id_bytes:
                identified_broker_key = key
                break
        if not identified_broker_key:
            identified_broker_key = message.get("broker_key")

        log_prefix = f"TCP RX [{identified_broker_key or client_id_hex}]:"

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

                # Notificar process monitor para cancelar grace period/retry
                if hasattr(self, 'mt5_monitor') and self.mt5_monitor:
                    self.mt5_monitor.on_broker_registered(broker_key)

                # Python é a fonte única do magic number — envia UMA vez ao conectar.
                # Até o SET_MAGIC_NUMBER chegar no EA, alien detection fica desabilitada.
                if self.tcp_router:
                    t = asyncio.create_task(
                        self.tcp_router.configure_magic_number(broker_key)
                    )
                    self._background_tasks.add(t)
                    t.add_done_callback(self._background_tasks.discard)

        elif msg_type == "INTERNAL" and event == "CLIENT_UNREGISTERED":
            broker_key = message.get("broker_key")
            if broker_key:
                self.log_message_received.emit(f"INFO: Corretora {broker_key} desconectada.")
                self.ping_button_state_changed.emit(False)
                self.heartbeat_active.pop(broker_key, None)
                trade_allowed_states.pop(broker_key, None)
                connection_status_states.pop(broker_key, None)

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

        elif msg_type == "STREAM" and event == "CONNECTION_STATUS":
            connected = message.get("connected")
            data = {
                "connected": connected,
                "broker_key": identified_broker_key,
                "timestamp_mql": message.get("timestamp_mql", 0)
            }
            if identified_broker_key and connected is not None:
                connection_status_states[identified_broker_key] = connected
            self.connection_status_received.emit(data)
            logger.info(f"CONNECTION_STATUS de {identified_broker_key}: {connected}")

        elif msg_type == "STREAM" and event == "TRADE_EVENT":
            # Reconstruir request e result a partir dos dados flattenados do MQL
            # (Contorna bug do Copy() em Json.mqh que sobrescreve m_key)
            request = {
                "action": message.get("request_action", 0),
                "order": message.get("request_order", 0),
                "symbol": message.get("request_symbol", ""),
                "volume": message.get("request_volume", 0),
                "price": message.get("request_price", 0),
                "sl": message.get("request_sl", 0),
                "tp": message.get("request_tp", 0),
                "deviation": message.get("request_deviation", 0),
                "type": message.get("request_type", 0),
                "type_filling": message.get("request_type_filling", 0),
                "comment": message.get("request_comment", ""),
                "position": message.get("request_position", 0),
            }
            result = {
                "retcode": message.get("result_retcode", 0),
                "deal": message.get("result_deal", 0),
                "order": message.get("result_order", 0),
                "volume": message.get("result_volume", 0),
                "price": message.get("result_price", 0),
                "comment": message.get("result_comment", ""),
            }

            trade_event_data = {
                "broker_key": identified_broker_key,
                "timestamp_mql": message.get("timestamp_mql", 0),
                "request": request,
                "result": result,
                "position_volume_remaining": message.get("position_volume_remaining"),
                "position_id": message.get("position_id", 0),
            }
            self.trade_event_received.emit(trade_event_data)
            logger.info(f"TRADE_EVENT de {identified_broker_key} - symbol={request.get('symbol', 'N/A')}")

            # Copytrade: se é do Master, replica para Slaves
            if self.copytrade_manager and self.broker_manager:
                if self.broker_manager.get_broker_role(identified_broker_key) == "master":
                    t = asyncio.create_task(
                        self.copytrade_manager.handle_master_trade_event(trade_event_data)
                    )
                    self._background_tasks.add(t)
                    t.add_done_callback(self._background_tasks.discard)

        elif msg_type == "STREAM" and event == "HEARTBEAT":
            broker_key = message.get("broker_key")
            role = message.get("role", "SLAVE")

            # Registrar heartbeat recebido
            if broker_key and broker_key not in self.heartbeat_active:
                self.heartbeat_active[broker_key] = True
                logger.debug(f"💓 Primeiro heartbeat de {broker_key} ({role})")

        elif msg_type == "STREAM" and event == "SLTP_MODIFIED":
            sltp_data = {
                "broker_key": identified_broker_key,
                "timestamp_mql": message.get("timestamp_mql", 0),
                "position_id": message.get("position_id", 0),
                "symbol": message.get("symbol", ""),
                "sl": message.get("sl", 0.0),
                "tp": message.get("tp", 0.0),
                "old_sl": message.get("old_sl", 0.0),
                "old_tp": message.get("old_tp", 0.0),
                "volume": message.get("volume", 0.0),
            }
            logger.info(
                f"SLTP_MODIFIED de {identified_broker_key} - pos_id={sltp_data['position_id']}, "
                f"sl={sltp_data['old_sl']:.5f}->{sltp_data['sl']:.5f}, "
                f"tp={sltp_data['old_tp']:.5f}->{sltp_data['tp']:.5f}"
            )

            if self.copytrade_manager and self.broker_manager:
                if self.broker_manager.get_broker_role(identified_broker_key) == "master":
                    t = asyncio.create_task(
                        self.copytrade_manager.handle_master_sltp_update(sltp_data)
                    )
                    self._background_tasks.add(t)
                    t.add_done_callback(self._background_tasks.discard)

        elif msg_type == "STREAM" and event == "ALIEN_TRADE":
            alien_data = {
                "broker_key": identified_broker_key,
                "deal": message.get("deal", 0),
                "deal_magic": message.get("deal_magic", 0),
                "expected_magic": message.get("expected_magic", 0),
                "symbol": message.get("symbol", ""),
                "volume": message.get("volume", 0),
                "deal_type": message.get("deal_type", ""),
                "timestamp_mql": message.get("timestamp_mql", 0),
            }
            logger.warning(
                f"ALIEN TRADE em {identified_broker_key}: "
                f"{alien_data['deal_type']} {alien_data['symbol']} "
                f"{alien_data['volume']} lotes (magic={alien_data['deal_magic']}, "
                f"esperado={alien_data['expected_magic']})"
            )
            self.log_message_received.emit(
                f"ALIEN TRADE detectado em {identified_broker_key}: "
                f"{alien_data['deal_type']} {alien_data['symbol']} "
                f"{alien_data['volume']} lotes — operacao NAO originada pelo CopyTrade!"
            )
            self.alien_trade_detected.emit(alien_data)

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
                    "positions": message.get("positions", []),
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
    def _track_task(self, task):
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def send_ping(self, broker_key: str):
        timestamp = time.time()
        t = asyncio.create_task(self.tcp_router.send_command_to_broker(
            broker_key, "PING",
            {"timestamp": timestamp},
            f"ping_{broker_key}_{timestamp}"
        ))
        self._track_task(t)

    def send_get_status_info(self, broker_key: str):
        timestamp = time.time()
        t = asyncio.create_task(self.tcp_router.send_command_to_broker(
            broker_key, "GET_STATUS_INFO",
            {"timestamp": timestamp},
            f"get_status_info_{broker_key}_{int(timestamp)}"
        ))
        self._track_task(t)

    # ──────────────────────────────────────────────
    # Bloco 5 - Auxiliares
    # ──────────────────────────────────────────────
    def get_trade_allowed_states(self):
        return trade_allowed_states.copy()

    def get_connection_status_states(self):
        return connection_status_states.copy()

    def clear_broker_status(self, broker_key):
        """Clear all cached status for a broker (on disconnect)."""
        trade_allowed_states.pop(broker_key, None)
        connection_status_states.pop(broker_key, None)
