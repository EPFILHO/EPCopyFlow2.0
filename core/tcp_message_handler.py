# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/tcp_message_handler.py
# Manipulador de mensagens TCP simplificado para copytrade.
# Removidos: signals de indicadores, OHLC, ticks, streams (não usados no copytrade).

import logging
import threading
import time
import asyncio
from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


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
    account_update_received = Signal(dict)

    # ──────────────────────────────────────────────
    # Bloco 1 - Inicialização
    # ──────────────────────────────────────────────
    def __init__(self, config, tcp_router, broker_manager=None, copytrade_manager=None,
                 engine=None, parent=None):
        """
        engine: instância de core.engine_thread.EngineThread. Necessária para
        que `send_ping` / `send_get_status_info` (chamados de botões da GUI na
        main thread) submetam coroutines ao loop do motor. Pode ser None em
        testes; nesse caso, esses métodos viram no-op com warning.
        """
        super().__init__(parent)
        self.config = config
        self.tcp_router = tcp_router
        self.broker_manager = broker_manager
        self.copytrade_manager = copytrade_manager
        self.engine = engine
        self.heartbeat_active = {}
        self._background_tasks: set = set()

        # Buffers de estado por corretora (broker_key -> bool)
        # Escritos pelo motor (handle_tcp_message) e lidos pela GUI (QTimer 2s).
        # Acesso protegido por _state_lock para evitar dirty reads.
        self._trade_allowed_states = {}
        self._connection_status_states = {}  # True = broker conectado, False = desconectado
        self._state_lock = threading.Lock()

    def set_copytrade_manager(self, copytrade_manager):
        self.copytrade_manager = copytrade_manager

    # ──────────────────────────────────────────────
    # Bloco 2 - Handler Principal
    # ──────────────────────────────────────────────
    @Slot(bytes, object)
    async def handle_tcp_message(self, client_id_bytes: bytes, message: dict):
        # Identifica broker
        client_id_hex = client_id_bytes.hex()
        identified_broker_key = None
        for key, client_id in self.tcp_router._clients.items():
            if client_id == client_id_bytes:
                identified_broker_key = key
                break
        if not identified_broker_key:
            identified_broker_key = message.get("broker_key")

        log_prefix = f"TCP RX [{identified_broker_key or client_id_hex}]:"

        # File logger (escreve em logs/epcopyflow_*.log se log_level=DEBUG)
        event = message.get("event")
        if event not in ("TICK", "HEARTBEAT"):
            logger.debug(f"{log_prefix} {message}")

        # IMPORTANTE: NÃO emit catch-all pro LogsPage da GUI aqui.
        # Eventos que o usuário deve ver são emitidos pontualmente abaixo
        # (REGISTER, UNREGISTER, ALIEN_TRADE, erros de RESPONSE) e pelo
        # CopyTradeManager via copy_trade_log.emit. ACCOUNT_UPDATE,
        # TRADE_EVENT e SLTP_MODIFIED têm signals dedicados; emitir tudo
        # como string aqui apenas polui a tela e gasta CPU em rajadas.

        msg_type = message.get("type")
        status = message.get("status")

        # ── SYSTEM events ──
        if msg_type == "SYSTEM" and event == "REGISTER":
            broker_key = message.get("broker_key")
            if broker_key:
                # Prefixo "REGISTER" preservado: main_window._handle_tcp_messages
                # usa string match nessa palavra para detectar conexão.
                self.log_message_received.emit(f"REGISTER: Corretora {broker_key} registrada.")
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
                # Prefixo "UNREGISTER" preservado para o detector em main_window.
                self.log_message_received.emit(f"UNREGISTER: Corretora {broker_key} desconectada.")
                self.ping_button_state_changed.emit(False)
                self.heartbeat_active.pop(broker_key, None)
                with self._state_lock:
                    self._trade_allowed_states.pop(broker_key, None)
                    self._connection_status_states.pop(broker_key, None)

        # ── STREAM events ──
        elif msg_type == "STREAM" and event == "TRADE_ALLOWED_UPDATE":
            data = {
                "trade_allowed": message.get("trade_allowed"),
                "broker_key": identified_broker_key,
                "timestamp_mql": message.get("timestamp_mql", 0)
            }
            if identified_broker_key and data["trade_allowed"] is not None:
                with self._state_lock:
                    self._trade_allowed_states[identified_broker_key] = data["trade_allowed"]
            self.trade_allowed_update_received.emit(data)

        elif msg_type == "STREAM" and event == "CONNECTION_STATUS":
            connected = message.get("connected")
            data = {
                "connected": connected,
                "broker_key": identified_broker_key,
                "timestamp_mql": message.get("timestamp_mql", 0)
            }
            if identified_broker_key and connected is not None:
                with self._state_lock:
                    self._connection_status_states[identified_broker_key] = connected
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
                "source": message.get("source", ""),
                "is_reversal": bool(message.get("is_reversal", False)),
                "old_direction": message.get("old_direction"),
                "new_direction": message.get("new_direction"),
                "old_volume": message.get("old_volume"),
                "new_volume": message.get("new_volume"),
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

        elif msg_type == "STREAM" and event == "ACCOUNT_UPDATE":
            self.account_update_received.emit({
                "broker_key":      identified_broker_key,
                "timestamp_mql":   message.get("timestamp_mql", 0),
                "balance":         message.get("balance", 0.0),
                "equity":          message.get("equity", 0.0),
                "margin":          message.get("margin", 0.0),
                "free_margin":     message.get("free_margin", 0.0),
                "currency":        message.get("currency", ""),
                "profit":          message.get("profit", 0.0),
                "positions_count": message.get("positions_count", 0),
            })

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
                logger.warning(f"Trade falhou para {broker_key}: {message.get('error_message', '?')}")

        else:
            # Só respostas de ERRO viram log visível na GUI. Sucesso OK genérico
            # (ex.: SET_MAGIC_NUMBER no startup) entrava aqui antes e poluía o
            # LogsPage sem trazer informação útil pro operador.
            if status != "OK":
                self.log_message_received.emit(
                    f"ERROR: {broker_key}: {message.get('error_message', '?')}")

    # ──────────────────────────────────────────────
    # Bloco 4 - Envio de Comandos
    # ──────────────────────────────────────────────
    def _submit_to_engine(self, coro):
        """Submete uma coroutine ao loop do motor. Os métodos send_* são
        chamados de slots Qt na main thread (sem event loop asyncio próprio),
        então precisam atravessar threads para entrar no loop do motor."""
        if self.engine is None:
            logger.warning("TcpMessageHandler sem engine — comando ignorado.")
            coro.close()
            return None
        return self.engine.submit(coro)

    def send_ping(self, broker_key: str):
        timestamp = time.time()
        self._submit_to_engine(self.tcp_router.send_command_to_broker(
            broker_key, "PING",
            {"timestamp": timestamp},
            f"ping_{broker_key}_{timestamp}"
        ))

    def send_get_status_info(self, broker_key: str):
        timestamp = time.time()
        self._submit_to_engine(self.tcp_router.send_command_to_broker(
            broker_key, "GET_STATUS_INFO",
            {"timestamp": timestamp},
            f"get_status_info_{broker_key}_{int(timestamp)}"
        ))

    # ──────────────────────────────────────────────
    # Bloco 5 - Auxiliares
    # ──────────────────────────────────────────────
    def get_trade_allowed_states(self):
        with self._state_lock:
            return self._trade_allowed_states.copy()

    def get_connection_status_states(self):
        with self._state_lock:
            return self._connection_status_states.copy()

    def clear_broker_status(self, broker_key):
        """Clear all cached status for a broker (on disconnect)."""
        with self._state_lock:
            self._trade_allowed_states.pop(broker_key, None)
            self._connection_status_states.pop(broker_key, None)
