# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# core/copytrade_manager.py
# Gerenciador de copytrade: recebe eventos do Master e replica para Slaves.
# Persistência em SQLite para histórico de cópias.

import sqlite3
import time
import logging
import asyncio
import hashlib
import datetime
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
        self.position_map = {}  # position_id (POSITION_IDENTIFIER) -> {slave_key: slave_ticket}
        self._emergency_active = False  # Suprime replicação durante emergency close
        self._emergency_completed_at = 0  # Timestamp do fim do emergency (grace period)
        self.symbol_specs_cache = {}  # (broker_key, symbol) -> {volume_min, volume_max, volume_step}
        self.db = sqlite3.connect(DB_FILE)
        self._init_db()

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

        # Migração: adicionar coluna direction se não existir
        try:
            self.db.execute("ALTER TABLE open_positions ADD COLUMN direction TEXT DEFAULT 'BUY'")
            self.db.commit()
            logger.info("Migração: coluna 'direction' adicionada a open_positions.")
        except sqlite3.OperationalError:
            pass  # Coluna já existe

        logger.info("Banco de dados SQLite inicializado (3 tabelas).")

    def validate_broker_for_copytrade(self, broker_key: str) -> tuple[bool, str]:
        """
        Valida se um broker pode usar CopyTrade.
        Retorna (sucesso: bool, mensagem: str)

        Chamado quando usuário tenta ATIVAR CopyTrade para um broker.
        """
        account_mode = self.broker_manager.get_account_mode(broker_key)
        mode_normalized = account_mode.lower()

        if mode_normalized not in ("netting", "netting account"):
            error_msg = (
                f"❌ {broker_key} está em modo '{account_mode}'\n\n"
                f"CopyTrade requer modo NETTING.\n\n"
                f"Por favor, altere no painel de configuração (Admin):\n"
                f"• Vá para: Configurações → Corretoras\n"
                f"• Mude o modo de '{account_mode}' para 'Netting'\n"
                f"• Salve as alterações"
            )
            logger.error(f"Validação falhou para {broker_key}: {error_msg}")
            return False, error_msg

        logger.info(f"✅ {broker_key} validado para CopyTrade (modo NETTING)")
        return True, "OK"

    async def detect_and_cache_account_mode(self, broker_key: str) -> str:
        """
        Detecta account mode real via GET_ACCOUNT_MODE do MT5.
        Salva em cache para uso posterior.
        Retorna: "Netting", "Hedging", "Exchange" ou "Unknown"
        """
        try:
            request_id = f"get_account_mode_{broker_key}_{int(time.time())}"
            logger.debug(f"🔍 Detectando account mode de {broker_key}...")

            response = await self.zmq_router.send_command_to_broker(
                broker_key, "GET_ACCOUNT_MODE", {}, request_id
            )

            if response.get("status") != "OK":
                logger.warning(f"  Falha ao detectar mode de {broker_key}: {response.get('message', '?')}")
                return "Unknown"

            account_mode = response.get("account_mode", "Unknown")
            logger.info(f"✅ Detectado: {broker_key} = {account_mode} mode")

            # Cachear no DB (atualizar broker_manager)
            self.broker_manager.cache_detected_mode(broker_key, account_mode)

            return account_mode

        except Exception as e:
            logger.error(f"Erro ao detectar account mode de {broker_key}: {e}")
            return "Unknown"

    async def detect_all_account_modes(self):
        """
        Detecta account modes para TODOS os brokers conectados.
        Chamado durante inicialização para cachear modos em brokers.json.
        """
        connected_brokers = self.broker_manager.get_connected_brokers()

        if not connected_brokers:
            logger.info("Nenhum broker conectado para detectar account modes.")
            return

        logger.info(f"🔍 Detectando account modes para {len(connected_brokers)} broker(s)...")

        for broker_key in connected_brokers:
            await self.detect_and_cache_account_mode(broker_key)

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

    async def _fetch_symbol_specs(self, broker_key: str, symbol: str) -> dict:
        """
        Busca VOLUME_MIN, VOLUME_MAX, VOLUME_STEP do símbolo via EA.
        Cacheia resultado para não repetir.
        Retorna dict com specs ou None se falhar.
        """
        cache_key = (broker_key, symbol)
        if cache_key in self.symbol_specs_cache:
            return self.symbol_specs_cache[cache_key]

        try:
            request_id = f"symbol_info_{broker_key}_{symbol}_{int(time.time())}"
            response = await self.zmq_router.send_command_to_broker(
                broker_key, "GET_SYMBOL_INFO", {"symbol": symbol}, request_id
            )

            if response.get("status") == "OK":
                specs = {
                    "volume_min": response.get("volume_min", 0.01),
                    "volume_max": response.get("volume_max", 100.0),
                    "volume_step": response.get("volume_step", 0.01),
                }
                self.symbol_specs_cache[cache_key] = specs
                logger.info(f"  📐 Symbol specs {symbol}@{broker_key}: min={specs['volume_min']}, max={specs['volume_max']}, step={specs['volume_step']}")
                return specs
            else:
                logger.warning(f"  ⚠️ Falha ao buscar specs de {symbol}@{broker_key}: {response.get('message', '?')}")
                return None
        except Exception as e:
            logger.error(f"  Erro ao buscar symbol specs de {symbol}@{broker_key}: {e}")
            return None

    def normalize_volume(self, volume: float, specs: dict, force_min: bool = False) -> float:
        """
        Normaliza volume de acordo com as especificações do símbolo.
        - Arredonda para o VOLUME_STEP mais próximo
        - Garante >= VOLUME_MIN e <= VOLUME_MAX
        - force_min=True (para PARTIAL_CLOSE): arredonda para CIMA ao step mais próximo,
          e se < VOLUME_MIN usa VOLUME_MIN. Parciais DEVEM acontecer.
        - force_min=False (para BUY/SELL): arredonda para BAIXO, retorna 0 se < VOLUME_MIN.
        """
        step = specs.get("volume_step", 0.01)
        vol_min = specs.get("volume_min", 0.01)
        vol_max = specs.get("volume_max", 100.0)

        if step <= 0:
            step = 0.01

        if force_min:
            # PARTIAL_CLOSE: arredondar para o mais próximo (round), mínimo = vol_min
            steps_count = round(volume / step)  # round normal (0.5 → 1)
            normalized = round(steps_count * step, 8)
            if normalized < vol_min:
                normalized = vol_min
                logger.info(f"    📐 Parcial forçado ao mínimo: {volume} → {normalized}")
        else:
            # BUY/SELL: arredondar para baixo (floor)
            steps_count = int(volume / step)
            normalized = round(steps_count * step, 8)
            if normalized < vol_min:
                logger.warning(f"    ⚠️ Volume {volume} → normalizado {normalized} < mínimo {vol_min}. Operação cancelada.")
                return 0.0

        # Verificar máximo
        if normalized > vol_max:
            normalized = round(int(vol_max / step) * step, 8)
            logger.warning(f"    ⚠️ Volume {volume} excede máximo. Limitado a {normalized}")

        return normalized

    def calculate_partial_close_lot(self, master_current_before: float, master_partial: float,
                                     slave_current: float) -> float:
        """
        Fechamento parcial proporcional baseado no volume ATUAL (não original).
        master_current_before: volume do master ANTES deste fechamento parcial
        master_partial: volume que o master está fechando agora
        slave_current: volume atual do slave
        """
        if master_current_before <= 0:
            return max(round(slave_current, 2), 0.01)
        ratio = master_partial / master_current_before
        lot = round(slave_current * ratio, 2)
        return max(lot, 0.01)

    # ──────────────────────────────────────────────
    # Bloco 3 - Processamento de Trade Events do Master
    # ──────────────────────────────────────────────
    # CHAVE DE DESIGN: Toda posição é rastreada pelo POSITION_IDENTIFIER do MQL5.
    # Este ID é imutável — conecta abertura, parciais e fechamento total.
    # O campo "master_ticket" no DB open_positions armazena o POSITION_IDENTIFIER (NÃO o deal).
    # O campo "master_ticket" no DB copytrade_history armazena o deal (log/auditoria).

    async def handle_master_trade_event(self, trade_event: dict):
        """
        Recebe TRADE_EVENT do Master EA e replica para todos os Slaves conectados.
        """
        master_broker = trade_event.get("broker_key")
        request_data = trade_event.get("request", {})
        result_data = trade_event.get("result", {})

        logger.info(f"🔍 handle_master_trade_event recebido de {master_broker}")

        # Suprimir replicação durante emergency close (evita double close)
        if self._emergency_active:
            logger.warning(f"  Replicação suprimida: emergency close ativo")
            return

        # Grace period: suprimir TRADE_EVENTs que chegam logo após emergency
        # (o master envia TRADE_EVENT async, pode chegar após _emergency_active=False)
        if self._emergency_completed_at > 0:
            elapsed = time.time() - self._emergency_completed_at
            if elapsed < 5.0:
                logger.warning(f"  Replicação suprimida: grace period pós-emergency ({elapsed:.1f}s < 5s)")
                return
            else:
                self._emergency_completed_at = 0  # Grace period expirado

        # Verifica se é realmente do master
        broker_role = self.broker_manager.get_broker_role(master_broker)
        if broker_role != "master":
            logger.debug(f"Trade event ignorado: {master_broker} não é master.")
            return

        # Filtrar ações não replicáveis antes de extrair tudo (reduz barulho no log)
        action = request_data.get("action", 0)
        if action != 1:  # Só TRADE_ACTION_DEAL (1) é replicável
            logger.debug(f"Trade event ignorado: action={action} (não é DEAL)")
            return

        # Extrair informações do trade
        symbol = request_data.get("symbol", "")
        volume = request_data.get("volume", 0)
        price = request_data.get("price", 0)
        sl = request_data.get("sl", 0)
        tp = request_data.get("tp", 0)
        order_type = request_data.get("type", 0)
        position_ticket = request_data.get("position", 0)
        retcode = result_data.get("retcode", 0)
        deal_ticket = result_data.get("deal", 0) or result_data.get("order", 0)
        position_volume_remaining = trade_event.get("position_volume_remaining")

        # POSITION_IDENTIFIER — chave universal (vem do EA)
        position_id = trade_event.get("position_id", 0)

        logger.info(f"  action={action}, symbol={symbol}, volume={volume}, retcode={retcode}, "
                     f"position_id={position_id}, deal={deal_ticket}, vol_remaining={position_volume_remaining}")

        # Só replica trades com sucesso (retcode 10009 = TRADE_RETCODE_DONE)
        if retcode != 10009 and retcode != 0:
            logger.warning(f"Trade event com retcode={retcode}, não será replicado.")
            return

        if not symbol:
            logger.warning("Trade event sem símbolo, ignorando.")
            return

        # Determinar tipo de ação para replicação
        trade_action = self._classify_trade_action(action, order_type, position_ticket, position_volume_remaining)
        logger.info(f"  trade_action={trade_action}")
        if not trade_action:
            logger.debug(f"Ação de trade não replicável: action={action}, type={order_type}")
            return

        # Validar que temos position_id (obrigatório para tracking)
        if not position_id:
            logger.error(f"  ❌ TRADE_EVENT sem position_id! EA pode estar desatualizado. deal={deal_ticket}")
            return

        log_msg = f"MASTER [{master_broker}]: {trade_action} {symbol} {volume} lotes (pos_id={position_id}, deal={deal_ticket})"
        self.copy_trade_log.emit(log_msg)
        logger.info(log_msg)

        # Atualizar tracking master no DB (CLOSE/PARTIAL_CLOSE)
        self._track_master_position(position_id, volume, trade_action)

        # Replica para cada slave conectado
        slaves = self.broker_manager.get_connected_slave_brokers()
        logger.info(f"  Slaves conectados: {slaves}")
        for slave_key in slaves:
            if self.is_slave_paused(slave_key):
                logger.warning(f"  Pulando {slave_key} (pausado)")
                continue

            await self._replicate_to_slave(
                slave_key, master_broker, deal_ticket, position_id,
                trade_action, symbol, volume, order_type, price, sl, tp
            )

    def _classify_trade_action(self, action: int, order_type: int, position_ticket: int,
                               position_volume_remaining=None) -> str:
        """
        Classifica a ação do trade com base nos dados do MQL5.
        action: TRADE_ACTION_DEAL=1, TRADE_ACTION_PENDING=5, TRADE_ACTION_SLTP=6,
                TRADE_ACTION_MODIFY=7, TRADE_ACTION_REMOVE=8
        order_type: 0=BUY, 1=SELL, 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP
        position_volume_remaining: volume restante após fechamento (0 = total, >0 = parcial)
        """
        if action == 1:  # TRADE_ACTION_DEAL (market order)
            if position_ticket > 0:
                if position_volume_remaining is not None and position_volume_remaining > 0:
                    return "PARTIAL_CLOSE"
                return "CLOSE"
            if order_type == 0:
                return "BUY"
            elif order_type == 1:
                return "SELL"
        return None

    def _track_master_position(self, position_id: int, volume: float, trade_action: str):
        """
        Atualiza tracking no DB para CLOSE/PARTIAL_CLOSE.
        Para BUY/SELL, as rows são criadas em _replicate_to_slave (uma por slave).
        """
        if trade_action == "CLOSE":
            self.db.execute(
                "UPDATE open_positions SET status = 'CLOSING' WHERE master_ticket = ? AND status = 'OPEN'",
                (position_id,)
            )
            self.db.commit()
            logger.debug(f"  📝 Posição marcada CLOSING: position_id={position_id}")

        elif trade_action == "PARTIAL_CLOSE":
            self.db.execute(
                "UPDATE open_positions SET master_volume_current = master_volume_current - ? WHERE master_ticket = ? AND status = 'OPEN'",
                (volume, position_id)
            )
            self.db.commit()
            logger.debug(f"  📝 Fechamento parcial master: position_id={position_id}, vol_fechado={volume}")

    def _normalize_and_cap(self, volume: float, slave_vol: float, symbol_specs: dict, force_min: bool = True) -> float:
        """Normaliza volume por specs do símbolo E limita ao que o slave tem (sell-through cap)."""
        if symbol_specs:
            volume = self.normalize_volume(volume, symbol_specs, force_min=force_min)
        if volume > slave_vol:
            logger.warning(f"    ⚠️ SELL-THROUGH CAP: {volume} > slave_vol={slave_vol}. Limitando a {slave_vol}")
            volume = slave_vol
        return volume

    def _get_slave_position_info(self, position_id: int, slave_key: str) -> dict:
        """Retorna info da posição do slave: {volume, direction}. None se não encontrado."""
        row = self.db.execute(
            "SELECT slave_volume_current, direction FROM open_positions WHERE master_ticket = ? AND slave_broker = ? AND status IN ('OPEN', 'CLOSING')",
            (position_id, slave_key)
        ).fetchone()
        if row:
            return {"volume": row[0], "direction": row[1] or "BUY"}
        return None

    def _get_slave_ticket(self, position_id: int, slave_key: str):
        """Busca slave_ticket pelo position_id — primeiro em memória, depois no DB."""
        # 1. Cache em memória (rápido)
        ticket = self.position_map.get(position_id, {}).get(slave_key)
        if ticket:
            return ticket
        # 2. Fallback: DB (persistente — sobrevive a restarts do Python)
        row = self.db.execute(
            "SELECT slave_ticket FROM open_positions WHERE master_ticket = ? AND slave_broker = ? AND status IN ('OPEN', 'CLOSING')",
            (position_id, slave_key)
        ).fetchone()
        return row[0] if row else None

    async def _replicate_to_slave(self, slave_key: str, master_broker: str,
                                   deal_ticket: int, position_id: int,
                                   trade_action: str, symbol: str, volume: float,
                                   order_type: int, price: float, sl: float, tp: float):
        """
        Envia comando de trade para um slave específico (NETTING mode).

        PADRÃO OURO NETTING: Só usa 3 comandos do EA:
          - TRADE_ORDER_TYPE_BUY  → abre/adiciona long ou reduz short
          - TRADE_ORDER_TYPE_SELL → abre/adiciona short ou reduz long
          - TRADE_POSITION_CLOSE_ID → fecha posição inteira por ticket

        NÃO usa TRADE_POSITION_PARTIAL (bugado no CTrade MQL5).
        PARTIAL_CLOSE é convertido em SELL/BUY com volume proporcional.
        """
        logger.info(f"  ➜ _replicate_to_slave: slave={slave_key}, action={trade_action}, symbol={symbol}, pos_id={position_id}")

        # Buscar specs do símbolo para validação de volume (cacheado)
        symbol_specs = await self._fetch_symbol_specs(slave_key, symbol)
        multiplier = self.broker_manager.get_lot_multiplier(slave_key)

        # Estado da posição (usado nos callbacks de sucesso)
        is_add = False
        is_reduce = False

        # Verificar se slave já tem posição aberta para este position_id
        pos_info = self._get_slave_position_info(position_id, slave_key)
        has_open_position = pos_info is not None and pos_info["volume"] > 0
        existing_slave_vol = pos_info["volume"] if pos_info else None
        existing_direction = pos_info["direction"] if pos_info else None

        # ── CLOSE TOTAL ──
        if trade_action == "CLOSE":
            slave_ticket = self._get_slave_ticket(position_id, slave_key)
            if not slave_ticket or not has_open_position:
                reason = "slave sem posição aberta" if not has_open_position else "sem mapeamento ticket"
                logger.warning(f"    ⚠️ CLOSE ignorado: {reason} (pos_id={position_id}, slave={slave_key})")
                self._insert_history(master_broker, deal_ticket, symbol, trade_action,
                                     volume, slave_key, 0, 0, "SKIPPED", reason)
                return

            command = "TRADE_POSITION_CLOSE_ID"
            payload = {"ticket": slave_ticket}
            slave_lot = existing_slave_vol
            logger.info(f"    CLOSE total: slave_ticket={slave_ticket}, slave_vol={existing_slave_vol}")

        # ── PARTIAL_CLOSE (convertido para SELL/BUY em NETTING) ──
        elif trade_action == "PARTIAL_CLOSE":
            if not has_open_position:
                logger.warning(f"    ⚠️ PARTIAL_CLOSE ignorado: slave sem posição aberta (pos_id={position_id}, slave={slave_key})")
                self._insert_history(master_broker, deal_ticket, symbol, trade_action,
                                     volume, slave_key, 0, 0, "SKIPPED", "slave sem posição aberta")
                return

            # Calcular volume proporcional
            # master_volume_current já decrementado em _track_master_position → compensar
            row = self.db.execute(
                "SELECT master_volume_current, slave_volume_current FROM open_positions WHERE master_ticket = ? AND slave_broker = ? AND status = 'OPEN'",
                (position_id, slave_key)
            ).fetchone()

            if row and row[1] > 0:
                master_current_before = row[0] + volume
                slave_lot = self.calculate_partial_close_lot(master_current_before, volume, row[1])
            else:
                slave_lot = self.calculate_slave_lot(volume, multiplier)

            # Normalizar + cap sell-through
            slave_lot = self._normalize_and_cap(slave_lot, existing_slave_vol, symbol_specs, force_min=True)
            if slave_lot <= 0:
                logger.warning(f"    ⚠️ PARTIAL_CLOSE ignorado: volume normalizado <= 0 (pos_id={position_id})")
                self._insert_history(master_broker, deal_ticket, symbol, trade_action,
                                     volume, slave_key, 0, 0, "SKIPPED", "volume=0 após normalização")
                return

            # NETTING: usar SELL/BUY em vez de TRADE_POSITION_PARTIAL
            # order_type do master: 0=BUY, 1=SELL — copiar direção
            command = "TRADE_ORDER_TYPE_SELL" if order_type == 1 else "TRADE_ORDER_TYPE_BUY"
            payload = {
                "symbol": symbol, "volume": float(slave_lot),
                "price": price, "sl": 0.0, "tp": 0.0,
                "deviation": 10, "comment": f"CT:{position_id}"
            }
            logger.info(f"    PARTIAL_CLOSE→{command}: vol={slave_lot} (slave_vol={existing_slave_vol})")

        # ── BUY/SELL (abertura, ADD ou REDUCE em NETTING) ──
        elif trade_action in ("BUY", "SELL"):
            slave_lot = self.calculate_slave_lot(volume, multiplier)

            if has_open_position:
                is_same_direction = (trade_action == existing_direction)
                if is_same_direction:
                    # ADD: BUY sobre BUY, ou SELL sobre SELL → aumentar posição
                    is_add = True
                    logger.info(f"    📊 NETTING ADD: slave tem {existing_slave_vol} {existing_direction}, adicionando {trade_action} {volume}")
                    if symbol_specs:
                        slave_lot = self.normalize_volume(slave_lot, symbol_specs, force_min=False)
                    if slave_lot <= 0:
                        logger.warning(f"    ❌ ADD ignorado: volume inválido para {symbol}")
                        self._insert_history(master_broker, deal_ticket, symbol, trade_action,
                                             volume, slave_key, 0, 0, "SKIPPED", "volume < mínimo do símbolo")
                        return
                else:
                    # REDUCE: SELL sobre BUY, ou BUY sobre SELL → reduzir posição
                    is_reduce = True
                    logger.info(f"    📊 NETTING REDUCE: slave tem {existing_slave_vol} {existing_direction}, reduzindo com {trade_action} {volume}")
                    slave_lot = self._normalize_and_cap(slave_lot, existing_slave_vol, symbol_specs, force_min=True)
                    if slave_lot <= 0:
                        logger.warning(f"    ⚠️ NETTING REDUCE ignorado: volume=0 após normalização")
                        self._insert_history(master_broker, deal_ticket, symbol, trade_action,
                                             volume, slave_key, 0, 0, "SKIPPED", "volume=0 após normalização")
                        return
            else:
                # Abertura nova
                if symbol_specs:
                    slave_lot = self.normalize_volume(slave_lot, symbol_specs, force_min=False)
                if slave_lot <= 0:
                    logger.warning(f"    ❌ Volume inválido para {symbol}. Operação cancelada.")
                    self._insert_history(master_broker, deal_ticket, symbol, trade_action,
                                         volume, slave_key, 0, 0, "SKIPPED", "volume < mínimo do símbolo")
                    return

            command = "TRADE_ORDER_TYPE_BUY" if trade_action == "BUY" else "TRADE_ORDER_TYPE_SELL"
            payload = {
                "symbol": symbol, "volume": float(slave_lot),
                "price": price, "sl": sl, "tp": tp,
                "deviation": 10, "comment": f"CT:{position_id}"
            }

        else:
            logger.warning(f"Ação não suportada: {trade_action}")
            return

        # ── Registrar PENDING e enviar ──
        record_id = self._insert_history(
            master_broker, deal_ticket, symbol, trade_action,
            volume, slave_key, 0, slave_lot, "PENDING"
        )
        log_msg = f"COPY [{slave_key}]: {trade_action} {symbol} {slave_lot} lotes"
        self.copy_trade_log.emit(log_msg)
        logger.info(f"    {log_msg} → {command}")

        request_id = f"trade_{slave_key}_{int(time.time())}"
        response = await self.zmq_router.send_command_to_broker(
            slave_key, command, payload, request_id
        )
        logger.info(f"    Resposta: {response}")

        # ── Processar resultado ──
        if response.get("status") == "OK":
            slave_result_ticket = response.get("order", 0) or response.get("deal", 0)
            self._update_history(record_id, "SUCCESS", slave_result_ticket)

            if trade_action == "CLOSE":
                self._on_close_success(position_id, slave_key)

            elif trade_action == "PARTIAL_CLOSE" or (trade_action in ("BUY", "SELL") and is_reduce):
                # Redução de posição (PARTIAL_CLOSE ou NETTING REDUCE)
                closed_vol = response.get("volume", slave_lot)
                if closed_vol >= existing_slave_vol:
                    logger.info(f"    📊 Redução → CLOSE total (slave_vol={existing_slave_vol}, closed={closed_vol})")
                    self._on_close_success(position_id, slave_key)
                else:
                    logger.info(f"    📊 Redução → PARTIAL (slave_vol={existing_slave_vol}, closed={closed_vol})")
                    self._on_partial_close_success(position_id, slave_key, closed_vol)

            elif trade_action in ("BUY", "SELL") and is_add:
                # ADD à posição existente (NETTING)
                added_vol = response.get("volume", slave_lot)
                logger.info(f"    📊 ADD ok: +{added_vol} lotes (slave_vol: {existing_slave_vol} → {existing_slave_vol + added_vol})")
                self._on_add_success(position_id, slave_key, added_vol, volume)

            else:
                # Abertura nova
                self._on_open_success(position_id, slave_key, slave_result_ticket, slave_lot, symbol, volume, direction=trade_action)

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

    # ── Callbacks de sucesso — mantém DB e position_map sincronizados ──

    def _on_open_success(self, position_id: int, slave_key: str,
                         slave_ticket: int, slave_lot: float,
                         symbol: str, master_volume: float, direction: str = "BUY"):
        """BUY/SELL confirmado pelo slave — cria row no open_positions e atualiza position_map."""
        now = time.time()
        # position_map: position_id → {slave_key: slave_ticket}
        if position_id not in self.position_map:
            self.position_map[position_id] = {}
        self.position_map[position_id][slave_key] = slave_ticket

        # Inserir row definitiva no open_positions (uma por slave, com dados reais)
        request_id = hashlib.sha256(
            f"{position_id}_{slave_key}_{int(now * 1000)}".encode()
        ).hexdigest()[:32]

        self.db.execute(
            """INSERT OR REPLACE INTO open_positions
               (master_ticket, slave_broker, slave_ticket, symbol,
                master_volume_original, master_volume_current, slave_volume_current,
                direction, status, request_id, opened_at, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)""",
            (position_id, slave_key, slave_ticket, symbol,
             master_volume, master_volume, slave_lot,
             direction, request_id, now, now)
        )
        self.db.commit()
        logger.debug(f"  ✅ Posição aberta: pos_id={position_id}, slave={slave_key}, slave_ticket={slave_ticket}, slave_lot={slave_lot}, dir={direction}")

    def _on_close_success(self, position_id: int, slave_key: str):
        """Fechamento total confirmado — marcar CLOSED, limpar position_map."""
        now = time.time()
        self.db.execute(
            "UPDATE open_positions SET status = 'CLOSED', closed_at = ? WHERE master_ticket = ? AND slave_broker = ?",
            (now, position_id, slave_key)
        )
        self.db.commit()
        if position_id in self.position_map:
            self.position_map[position_id].pop(slave_key, None)
            if not self.position_map[position_id]:
                del self.position_map[position_id]
        logger.debug(f"  ✅ Posição FECHADA: pos_id={position_id}, slave={slave_key}")

    def _on_add_success(self, position_id: int, slave_key: str, added_volume: float, master_volume: float):
        """ADD à posição confirmado — incrementar volumes no DB."""
        now = time.time()
        self.db.execute(
            """UPDATE open_positions
               SET slave_volume_current = slave_volume_current + ?,
                   master_volume_current = master_volume_current + ?,
                   last_heartbeat = ?
               WHERE master_ticket = ? AND slave_broker = ? AND status = 'OPEN'""",
            (added_volume, master_volume, now, position_id, slave_key)
        )
        self.db.commit()
        logger.debug(f"  ✅ ADD ok: pos_id={position_id}, slave={slave_key}, +{added_volume} lotes")

    def _on_partial_close_success(self, position_id: int, slave_key: str, closed_volume: float):
        """Fechamento parcial confirmado — decrementar volume do slave."""
        now = time.time()
        self.db.execute(
            """UPDATE open_positions
               SET slave_volume_current = MAX(0, slave_volume_current - ?), last_heartbeat = ?
               WHERE master_ticket = ? AND slave_broker = ? AND status = 'OPEN'""",
            (closed_volume, now, position_id, slave_key)
        )
        self.db.commit()
        logger.debug(f"  ✅ Parcial ok: pos_id={position_id}, slave={slave_key}, vol_fechado={closed_volume}")

    # ──────────────────────────────────────────────
    # Bloco 4 - Fechamento de Emergência
    # ──────────────────────────────────────────────
    async def emergency_close_all(self):
        """Fecha TODAS as posições em TODOS os MT5s (master + slaves)."""
        logger.warning("EMERGÊNCIA: Fechando todas as posições!")
        self._emergency_active = True
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

            # Parsear posições do formato flattenado (pos_0_ticket, pos_1_ticket, ...)
            positions = []
            positions_count = response.get("positions_count", 0)
            for i in range(positions_count):
                prefix = f"pos_{i}_"
                ticket = response.get(f"{prefix}ticket", 0)
                symbol = response.get(f"{prefix}symbol", "")
                if ticket and ticket > 0:
                    positions.append({"ticket": ticket, "symbol": symbol})

            for pos in positions:
                ticket = pos.get("ticket", 0)
                symbol = pos.get("symbol", "")
                if ticket > 0:
                    close_id = f"close_{broker_key}_{ticket}_{int(time.time())}"
                    close_response = await self.zmq_router.send_command_to_broker(
                        broker_key, "TRADE_POSITION_CLOSE_ID",
                        {"ticket": ticket, "emergency": True}, close_id
                    )
                    if close_response.get("status") == "OK":
                        total_closed += 1
                        self.copy_trade_log.emit(
                            f"EMERGÊNCIA: Fechado {symbol} ticket={ticket} em {broker_key}")
                    else:
                        error = close_response.get("message", "erro")
                        errors.append(f"{broker_key}/{symbol}: {error}")

        # Marcar TODAS as posições como PANIC no DB (diferencia de CLOSED normal)
        now = time.time()
        self.db.execute(
            "UPDATE open_positions SET status = 'PANIC', closed_at = ? WHERE status IN ('OPEN', 'SYNCING', 'CLOSING')",
            (now,)
        )
        self.db.commit()
        logger.info("  📝 Todas as posições marcadas como PANIC no DB")

        # Limpa mapa de posições
        self.position_map.clear()
        self._emergency_completed_at = time.time()  # Inicia grace period de 5s
        self._emergency_active = False

        if errors:
            msg = f"Fechadas {total_closed} posições. Erros: {'; '.join(errors)}"
            self.emergency_completed.emit(False, msg)
        else:
            msg = f"EMERGÊNCIA concluída: {total_closed} posições fechadas."
            self.emergency_completed.emit(True, msg)

        self.copy_trade_log.emit(msg)
        logger.warning(msg)

    # ──────────────────────────────────────────────
    # Bloco 5 - Histórico e Estatísticas (SQLite)
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
