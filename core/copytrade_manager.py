# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/copytrade_manager.py
# Gerenciador de copytrade: recebe eventos do Master e replica para Slaves.
# Persistência em SQLite para histórico de cópias.

import sqlite3
import time
import logging
import asyncio
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

DB_FILE = "copytrade_history.db"


class CopyTradeManager(QObject):
    copy_trade_executed = Signal(dict)
    copy_trade_failed = Signal(dict)
    copy_trade_log = Signal(str)
    emergency_completed = Signal(bool, str)  # (success, message)

    # ──────────────────────────────────────────────
    # Bloco 1 - Inicialização e Banco de Dados
    # ──────────────────────────────────────────────
    def __init__(self, broker_manager, zmq_router, parent=None):
        super().__init__(parent)
        self.broker_manager = broker_manager
        self.zmq_router = zmq_router
        self.position_map = {}  # master_ticket -> {slave_key: slave_ticket}
        self.db = sqlite3.connect(DB_FILE)
        self._init_db()
        self._validate_account_modes()

        # Heartbeat control
        self.heartbeat_task = None
        self.heartbeat_running = False

        logger.info("CopyTradeManager inicializado.")

    def _init_db(self):
        # Tabela original: histórico de replicações
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS copytrade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                master_broker TEXT NOT NULL,
                master_ticket INTEGER,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                master_lot REAL NOT NULL,
                slave_broker TEXT NOT NULL,
                slave_ticket INTEGER,
                slave_lot REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                error_message TEXT DEFAULT ''
            )
        """)

        # Tabela: Posições abertas (rastreamento ativo)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS open_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_ticket INTEGER NOT NULL,
                slave_broker TEXT NOT NULL,
                slave_ticket INTEGER,
                symbol TEXT NOT NULL,

                master_volume_original REAL NOT NULL,
                master_volume_current REAL NOT NULL,
                slave_volume_current REAL NOT NULL,

                status TEXT NOT NULL,  -- SYNCING / OPEN / SYNCED / CLOSING / CLOSED
                request_id TEXT UNIQUE,

                opened_at REAL NOT NULL,
                synced_at REAL,
                last_heartbeat REAL,
                closed_at REAL,

                sync_attempts INTEGER DEFAULT 0,
                last_error TEXT,

                UNIQUE(master_ticket, slave_broker)
            )
        """)

        # Tabela: Status de cada slave (paused/active)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS slave_status (
                slave_broker TEXT PRIMARY KEY,
                status TEXT NOT NULL,  -- ACTIVE / PAUSED
                paused_reason TEXT,
                paused_at REAL,
                paused_by_ticket INTEGER,
                can_resume_at REAL,
                last_heartbeat REAL
            )
        """)

        self.db.commit()
        logger.info("Banco de dados SQLite inicializado (3 tabelas).")

    def _validate_account_modes(self):
        """
        Valida que todas as contas configuradas são NETTING.
        CopyTrade só suporta NETTING (não HEDGE) para simplificar lógica de replicação.
        """
        brokers = self.broker_manager.get_brokers()

        for broker_key, broker_data in brokers.items():
            account_mode = self.broker_manager.get_account_mode(broker_key)

            # Normalizar para comparação (case-insensitive)
            mode_normalized = account_mode.lower()

            if mode_normalized not in ("netting", "netting account"):
                logger.error(f"❌ {broker_key}: modo '{account_mode}' não suportado")
                logger.error(f"   CopyTrade requer contas em NETTING mode")
                logger.error(f"   Configure a conta como NETTING em brokers.json")
                raise ValueError(
                    f"CopyTrade não suporta {account_mode}. "
                    f"Configure {broker_key} como NETTING."
                )

        logger.info(f"✅ Validação: Todas as {len(brokers)} contas estão em NETTING mode")

    # ──────────────────────────────────────────────
    # Bloco 1.5 - Gerenciamento de Status de Slaves
    # ──────────────────────────────────────────────
    def get_slave_status(self, slave_key: str) -> dict:
        """Retorna status do slave (ACTIVE/PAUSED)."""
        cursor = self.db.execute(
            "SELECT status, paused_reason, paused_at FROM slave_status WHERE slave_broker = ?",
            (slave_key,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "status": row[0],
                "paused_reason": row[1],
                "paused_at": row[2]
            }
        return {"status": "ACTIVE", "paused_reason": None, "paused_at": None}

    def is_slave_paused(self, slave_key: str) -> bool:
        """Verifica se slave está pausado."""
        status = self.get_slave_status(slave_key)
        return status["status"] == "PAUSED"

    def pause_slave(self, slave_key: str, reason: str, ticket: int = None):
        """
        Pausa copytrader para um slave específico.
        reason: ex. "ALIEN_OPERATION", "MANUAL"
        """
        now = time.time()
        self.db.execute(
            """INSERT OR REPLACE INTO slave_status
               (slave_broker, status, paused_reason, paused_at, paused_by_ticket)
               VALUES (?, ?, ?, ?, ?)""",
            (slave_key, "PAUSED", reason, now, ticket)
        )
        self.db.commit()
        logger.warning(f"⏸️ CopyTrade PAUSADO para {slave_key}: {reason}")

    def resume_slave(self, slave_key: str):
        """Reativa copytrader para um slave."""
        self.db.execute(
            """UPDATE slave_status SET status = ?, paused_reason = ?, paused_at = NULL
               WHERE slave_broker = ?""",
            ("ACTIVE", None, slave_key)
        )
        self.db.commit()
        logger.info(f"▶️ CopyTrade REATIVADO para {slave_key}")

    # ──────────────────────────────────────────────
    # Bloco 2 - Cálculo de Lotes
    # ──────────────────────────────────────────────
    def calculate_slave_lot(self, master_lot: float, multiplier: float) -> float:
        """Calcula lote do slave. Arredonda para 2 casas decimais, mínimo 0.01."""
        lot = round(master_lot * multiplier, 2)
        return max(lot, 0.01)

    def calculate_partial_close_lot(self, master_original: float, master_partial: float,
                                     slave_current: float) -> float:
        """Fechamento parcial proporcional."""
        if master_original <= 0:
            return max(round(slave_current, 2), 0.01)
        ratio = master_partial / master_original
        lot = round(slave_current * ratio, 2)
        return max(lot, 0.01)

    # ──────────────────────────────────────────────
    # Bloco 3 - Processamento de Trade Events do Master
    # ──────────────────────────────────────────────
    async def handle_master_trade_event(self, trade_event: dict):
        """
        Recebe TRADE_EVENT do Master EA e replica para todos os Slaves conectados.
        """
        master_broker = trade_event.get("broker_key")
        request_data = trade_event.get("request", {})
        result_data = trade_event.get("result", {})

        logger.info(f"🔍 handle_master_trade_event recebido de {master_broker}")
        logger.info(f"  request_data keys: {list(request_data.keys())}")
        logger.info(f"  result_data keys: {list(result_data.keys())}")

        # Verifica se é realmente do master
        broker_role = self.broker_manager.get_broker_role(master_broker)
        logger.info(f"  Broker role: {broker_role}")
        if broker_role != "master":
            logger.debug(f"Trade event ignorado: {master_broker} não é master.")
            return

        # Extrair informações do trade
        action = request_data.get("action", 0)
        symbol = request_data.get("symbol", "")
        volume = request_data.get("volume", 0)
        price = request_data.get("price", 0)
        sl = request_data.get("sl", 0)
        tp = request_data.get("tp", 0)
        order_type = request_data.get("type", 0)
        position_ticket = request_data.get("position", 0)
        retcode = result_data.get("retcode", 0)
        master_ticket = result_data.get("deal", 0) or result_data.get("order", 0)

        logger.info(f"  action={action}, symbol={symbol}, volume={volume}, retcode={retcode}")

        # Só replica trades com sucesso (retcode 10009 = TRADE_RETCODE_DONE)
        if retcode != 10009 and retcode != 0:
            logger.warning(f"Trade event com retcode={retcode}, não será replicado.")
            return

        if not symbol:
            logger.warning("Trade event sem símbolo, ignorando.")
            return

        # Determinar tipo de ação para replicação
        trade_action = self._classify_trade_action(action, order_type, position_ticket)
        logger.info(f"  trade_action={trade_action}")
        if not trade_action:
            logger.debug(f"Ação de trade não replicável: action={action}, type={order_type}")
            return

        log_msg = f"MASTER [{master_broker}]: {trade_action} {symbol} {volume} lotes (ticket={master_ticket})"
        self.copy_trade_log.emit(log_msg)
        logger.info(log_msg)

        # Replica para cada slave conectado
        slaves = self.broker_manager.get_connected_slave_brokers()
        logger.info(f"  Slaves conectados: {slaves}")
        for slave_key in slaves:
            await self._replicate_to_slave(
                slave_key, master_broker, master_ticket,
                trade_action, symbol, volume, price, sl, tp,
                position_ticket
            )

    def _classify_trade_action(self, action: int, order_type: int, position_ticket: int) -> str:
        """
        Classifica a ação do trade com base nos dados do MQL5.
        action: TRADE_ACTION_DEAL=1, TRADE_ACTION_PENDING=5, TRADE_ACTION_SLTP=6,
                TRADE_ACTION_MODIFY=7, TRADE_ACTION_REMOVE=8
        order_type: 0=BUY, 1=SELL, 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP
        """
        if action == 1:  # TRADE_ACTION_DEAL (market order)
            if position_ticket > 0:
                return "CLOSE"  # Fechamento de posição
            if order_type == 0:
                return "BUY"
            elif order_type == 1:
                return "SELL"
        return None  # Outras ações não são replicadas por enquanto

    async def _replicate_to_slave(self, slave_key: str, master_broker: str,
                                   master_ticket: int, trade_action: str,
                                   symbol: str, volume: float, price: float,
                                   sl: float, tp: float, position_ticket: int):
        """Envia comando de trade para um slave específico."""
        logger.info(f"  ➜ _replicate_to_slave: slave={slave_key}, action={trade_action}, symbol={symbol}")

        multiplier = self.broker_manager.get_lot_multiplier(slave_key)
        slave_lot = self.calculate_slave_lot(volume, multiplier)
        logger.info(f"    multiplier={multiplier}, slave_lot={slave_lot}")

        # Determinar comando
        if trade_action == "BUY":
            command = "TRADE_ORDER_TYPE_BUY"
            payload = {
                "symbol": symbol,
                "volume": float(slave_lot),
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 10,
                "comment": f"CT:{master_ticket}"
            }
        elif trade_action == "SELL":
            command = "TRADE_ORDER_TYPE_SELL"
            payload = {
                "symbol": symbol,
                "volume": float(slave_lot),
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 10,
                "comment": f"CT:{master_ticket}"
            }
        elif trade_action == "CLOSE":
            command = "TRADE_POSITION_CLOSE"
            # Buscar ticket do slave correspondente ao master_ticket
            slave_ticket = self.position_map.get(position_ticket, {}).get(slave_key)
            if slave_ticket:
                payload = {"ticket": slave_ticket}
            else:
                # Fechar por símbolo se não temos o mapeamento
                payload = {"symbol": symbol}
        else:
            logger.warning(f"Ação não suportada para replicação: {trade_action}")
            return

        # Registra no SQLite como PENDING
        record_id = self._insert_history(
            master_broker, master_ticket, symbol, trade_action,
            volume, slave_key, 0, slave_lot, "PENDING"
        )

        log_msg = f"COPY [{slave_key}]: {trade_action} {symbol} {slave_lot} lotes"
        self.copy_trade_log.emit(log_msg)
        logger.info(log_msg)

        # Envia comando para o slave
        request_id = f"trade_{slave_key}_{int(time.time())}"
        logger.info(f"    Enviando comando: {command}, payload: {payload}")
        response = await self.zmq_router.send_command_to_broker(
            slave_key, command, payload, request_id
        )
        logger.info(f"    Resposta recebida: {response}")

        # Atualiza status no histórico
        if response.get("status") == "OK":
            slave_result_ticket = response.get("order", 0) or response.get("deal", 0)
            self._update_history(record_id, "SUCCESS", slave_result_ticket)
            # Mapeia posições para fechamento futuro
            if trade_action in ("BUY", "SELL") and master_ticket:
                if master_ticket not in self.position_map:
                    self.position_map[master_ticket] = {}
                self.position_map[master_ticket][slave_key] = slave_result_ticket
            self.copy_trade_executed.emit({
                "slave": slave_key, "symbol": symbol,
                "action": trade_action, "lot": slave_lot
            })
        else:
            error = response.get("message", "Erro desconhecido")
            self._update_history(record_id, "FAILED", 0, error)
            self.copy_trade_failed.emit({
                "slave": slave_key, "symbol": symbol,
                "action": trade_action, "error": error
            })
            logger.error(f"Falha ao replicar para {slave_key}: {error}")

    # ──────────────────────────────────────────────
    # Bloco 4 - Heartbeat de Sincronização
    # ──────────────────────────────────────────────
    def start_heartbeat(self):
        """Inicia o heartbeat em background."""
        if self.heartbeat_running:
            logger.debug("Heartbeat já está rodando")
            return

        self.heartbeat_running = True
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("✅ Heartbeat de sincronização iniciado")

    def stop_heartbeat(self):
        """Para o heartbeat."""
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        self.heartbeat_running = False
        logger.info("⏹️ Heartbeat de sincronização parado")

    async def _heartbeat_loop(self):
        """
        Loop principal do heartbeat: a cada HEARTBEAT_INTERVAL segundos,
        valida sincronização de posições e detecta operações alienígenas.
        """
        import configparser

        # Ler intervalo do config
        config = configparser.ConfigParser()
        config.read("config.ini")
        heartbeat_interval = int(config.get("CopyTrade", "heartbeat_interval", fallback="5"))

        logger.info(f"🔄 Heartbeat loop iniciado (intervalo: {heartbeat_interval}s)")

        while self.heartbeat_running:
            try:
                await asyncio.sleep(heartbeat_interval)

                if not self.heartbeat_running:
                    break

                logger.debug("🔄 Heartbeat: iniciando validação")
                await self._reconcile_positions()

            except asyncio.CancelledError:
                logger.info("Heartbeat cancelado")
                break
            except Exception as e:
                logger.error(f"❌ Erro no heartbeat: {e}", exc_info=True)

    async def _reconcile_positions(self):
        """
        Valida sincronização:
        - Detecta operações alienígenas
        - Atualiza heartbeat no DB
        """
        try:
            # Detectar operações alienígenas
            await self._detect_alien_operations()

            # Atualizar heartbeat timestamp
            now = time.time()
            self.db.execute(
                "UPDATE slave_status SET last_heartbeat = ? WHERE status = 'ACTIVE'",
                (now,)
            )
            self.db.commit()

        except Exception as e:
            logger.error(f"Erro ao reconciliar: {e}")

    async def _detect_alien_operations(self):
        """
        Detecta operações manuais no Slave que não estão mapeadas.
        Se encontrar, pausa copytrader para aquele slave.
        """
        master_brokers = [k for k, v in self.broker_manager.get_brokers().items()
                         if self.broker_manager.get_broker_role(k) == "master"]
        slave_brokers = [k for k, v in self.broker_manager.get_brokers().items()
                        if self.broker_manager.get_broker_role(k) == "slave"]

        if not master_brokers or not slave_brokers:
            return

        master_key = master_brokers[0]

        for slave_key in slave_brokers:
            # Pular se já pausado
            if self.is_slave_paused(slave_key):
                continue

            try:
                # Pedir posições do Slave
                slave_positions = await self._get_slave_positions(slave_key)

                if not slave_positions:
                    continue

                # Ler mapeamento do DB
                db_rows = self.db.execute(
                    "SELECT slave_ticket FROM open_positions WHERE slave_broker = ? AND status != 'CLOSED'",
                    (slave_key,)
                ).fetchall()

                mapped_tickets = {row[0] for row in db_rows}

                # Verificar se há tickets no Slave não mapeados
                for ticket, position in slave_positions.items():
                    if ticket not in mapped_tickets:
                        logger.warning(f"🚨 OPERAÇÃO ALIENÍGENA detectada em {slave_key}!")
                        logger.warning(f"   Ticket: {ticket}, Símbolo: {position.get('symbol')}, Volume: {position.get('volume')}")

                        # Pausar copytrader
                        self.pause_slave(slave_key, "ALIEN_OPERATION", ticket)
                        self.copy_trade_log.emit(
                            f"⚠️ CopyTrade PAUSADO em {slave_key}: operação manual detectada"
                        )
                        break

            except Exception as e:
                logger.error(f"Erro ao detectar alienígenas em {slave_key}: {e}")

    async def _get_slave_positions(self, slave_key: str) -> dict:
        """
        Retorna dict de posições abertas do Slave: {ticket: {symbol, volume, ...}}
        Placeholder: implementação real requer comunicação com EA.
        """
        # TODO: Implementar comunicação com Slave EA para pedir posições abertas
        # Por enquanto, retorna vazio
        return {}

    # ──────────────────────────────────────────────
    # Bloco 5 - Fechamento de Emergência
    # ──────────────────────────────────────────────
    async def emergency_close_all(self):
        """Fecha TODAS as posições em TODOS os MT5s (master + slaves)."""
        logger.warning("EMERGÊNCIA: Fechando todas as posições!")
        self.copy_trade_log.emit("EMERGÊNCIA: Iniciando fechamento de todas as posições...")

        connected = self.broker_manager.get_connected_brokers()
        total_closed = 0
        errors = []

        for broker_key in connected:
            # Solicita posições abertas
            request_id = f"positions_{broker_key}_{int(time.time())}"
            response = await self.zmq_router.send_command_to_broker(
                broker_key, "POSITIONS", {}, request_id
            )

            if response.get("status") != "OK":
                errors.append(f"{broker_key}: falha ao obter posições")
                continue

            positions = response.get("positions", [])
            if not positions:
                positions = response.get("data", [])
            if not positions:
                positions = response.get("result", [])

            for pos in positions:
                ticket = pos.get("ticket", 0)
                symbol = pos.get("symbol", "")
                if ticket > 0:
                    close_id = f"close_{broker_key}_{ticket}_{int(time.time())}"
                    close_response = await self.zmq_router.send_command_to_broker(
                        broker_key, "TRADE_POSITION_CLOSE_ID",
                        {"ticket": ticket}, close_id
                    )
                    if close_response.get("status") == "OK":
                        total_closed += 1
                        self.copy_trade_log.emit(
                            f"EMERGÊNCIA: Fechado {symbol} ticket={ticket} em {broker_key}")
                    else:
                        error = close_response.get("message", "erro")
                        errors.append(f"{broker_key}/{symbol}: {error}")

        # Limpa mapa de posições
        self.position_map.clear()

        if errors:
            msg = f"Fechadas {total_closed} posições. Erros: {'; '.join(errors)}"
            self.emergency_completed.emit(False, msg)
        else:
            msg = f"EMERGÊNCIA concluída: {total_closed} posições fechadas."
            self.emergency_completed.emit(True, msg)

        self.copy_trade_log.emit(msg)
        logger.warning(msg)

    # ──────────────────────────────────────────────
    # Bloco 5 - Histórico (SQLite)
    # ──────────────────────────────────────────────
    def _insert_history(self, master_broker, master_ticket, symbol, action,
                        master_lot, slave_broker, slave_ticket, slave_lot, status,
                        error_message=""):
        cursor = self.db.execute(
            """INSERT INTO copytrade_history
               (timestamp, master_broker, master_ticket, symbol, action,
                master_lot, slave_broker, slave_ticket, slave_lot, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), master_broker, master_ticket, symbol, action,
             master_lot, slave_broker, slave_ticket, slave_lot, status, error_message)
        )
        self.db.commit()
        return cursor.lastrowid

    def _update_history(self, record_id, status, slave_ticket=None, error_message=""):
        if slave_ticket is not None:
            self.db.execute(
                "UPDATE copytrade_history SET status=?, slave_ticket=?, error_message=? WHERE id=?",
                (status, slave_ticket, error_message, record_id)
            )
        else:
            self.db.execute(
                "UPDATE copytrade_history SET status=?, error_message=? WHERE id=?",
                (status, error_message, record_id)
            )
        self.db.commit()

    def get_trade_history(self, broker_key=None, limit=100):
        """Retorna histórico de cópias, filtrado opcionalmente por broker."""
        if broker_key:
            rows = self.db.execute(
                """SELECT * FROM copytrade_history
                   WHERE master_broker=? OR slave_broker=?
                   ORDER BY timestamp DESC LIMIT ?""",
                (broker_key, broker_key, limit)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM copytrade_history ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
        columns = ["id", "timestamp", "master_broker", "master_ticket", "symbol",
                    "action", "master_lot", "slave_broker", "slave_ticket",
                    "slave_lot", "status", "error_message"]
        return [dict(zip(columns, row)) for row in rows]

    def get_today_stats(self):
        """Retorna estatísticas do dia (trades copiados, sucesso, falha)."""
        import datetime
        today_start = datetime.datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        row = self.db.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed
               FROM copytrade_history WHERE timestamp >= ?""",
            (today_start,)
        ).fetchone()
        return {"total": row[0] or 0, "success": row[1] or 0, "failed": row[2] or 0}
