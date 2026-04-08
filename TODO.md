# EPCopyFlow 2.0 - TODO

## Prioridade CRÍTICA (Bugs encontrados em teste)
- [x] **CRASH LOOP** — Process monitor reinicia MT5 infinitamente ✅ RESOLVIDO
  - Configurado max_retries + backoff no [ProcessMonitor] do config.ini

- [x] **MT5 CARDS CINZA** — Resolvido com fix do crash loop ✅

- [ ] **BROKER_STATUS STALE** — Quando process monitor reinicia MT5, `broker_status[key]` pode ficar True
  - O EA não envia UNREGISTER em tempo antes do restart
  - Solução: process monitor deve chamar `clear_broker_status()` do zmq_message_handler ao detectar morte
  - Ou fazer isso via signal (refatoração futura no TODO)

## Bugs Descobertos em Teste Funcional
- [x] **SQL BINDING ERROR** — INSERT em open_positions com número errado de placeholders ✅ FIXADO
  - Commit: `0b467ca`

- [x] **FECHAMENTO PARCIAL FECHA TUDO** ✅ FIXADO
  - Causa: falta de mapeamento Master_ticket → Slave_ticket + uso de TRADE_POSITION_PARTIAL (bugado no CTrade)
  - Fix: rastreamento por POSITION_IDENTIFIER, PARTIAL_CLOSE convertido para SELL/BUY com volume proporcional
  - Eliminado TRADE_POSITION_PARTIAL do fluxo Python (EA mantém suporte legado com fix de retcode)

- [x] **SELL-THROUGH (P0)** — Slave podia ficar com posição negativa ✅ FIXADO
  - Fix: `_normalize_and_cap()` limita volume ao `slave_volume_current` do DB
  - CLOSE bloqueado se slave_vol=0, PARTIAL_CLOSE capped ao slave_vol

- [x] **VOLUME VALIDATION (P1)** — Volumes inválidos para specs do símbolo ✅ FIXADO
  - Fix: EA `GET_SYMBOL_INFO` → cache Python → `normalize_volume()` com floor/ceil modes
  - `force_min=True` para reduces/closes (arredonda UP, força mínimo)
  - `force_min=False` para opens (arredonda DOWN, cancela se < mínimo)

- [x] **NETTING ADD vs REDUCE INVERTIDO** — BUY sobre BUY era tratado como REDUCE ✅ FIXADO
  - Causa: sem tracking de direção (BUY/SELL) da posição existente
  - Fix: coluna `direction` em open_positions, comparação com `trade_action`
  - BUY sobre BUY = ADD (incrementa), SELL sobre BUY = REDUCE (diminui)

- [x] **CTrade PositionClosePartial retorna false em sucesso** ✅ FIXADO no EA
  - Bug do MQL5: retcode=10009 (DONE) mas método retorna false
  - Fix: EA agora verifica `trade.ResultRetcode()` em vez do return value

- [x] **ALIEN OPERATIONS DETECTADAS (P2)** — Detecção via magic number no EA ✅ FIXADO
  - EA recebe magic number do Python (SET_MAGIC_NUMBER) e seta no CTrade
  - OnTradeTransaction: DEAL_MAGIC != nosso magic → envia ALIEN_TRADE event
  - Python: handler loga warning + emite signal alien_trade_detected para UI
  - Abordagem mais robusta que heartbeat: detecta em tempo real, sem race conditions

- [x] **JSON TRUNCATION (~255 chars)** ✅ FIXADO
  - Causa raiz: MQL5 trunca string em `return` de funções (~255 chars)
  - `SerializeTo(string &out)` preenchia por referência, mas `return msg;` em
    `RobustJsonSerialize()` re-truncava ao retornar
  - Fix: `RobustJsonSerialize` agora é `void` com `string &out` por referência
  - Toda a cadeia é por referência: `SerializeTo() → out → SendJsonMessage()`
  - **REQUER recompilação do EA no MetaEditor**

- [x] **EMERGENCY CLOSE DOUBLE-CLOSE (race condition)** ✅ FIXADO
  - Causa: `_emergency_active=False` resetava antes do TRADE_EVENT async do master chegar
  - Fix duplo:
    1. Marca todas `open_positions` como `PANIC` no DB antes de resetar flag
       → `_get_slave_position_info()` retorna None → CLOSE ignorado
    2. Grace period de 5s via timestamp (`_emergency_completed_at`)
       → TRADE_EVENTs atrasados suprimidos com log de aviso

- [x] **EMERGENCY CLOSE SEM LOG NO DB** ✅ FIXADO
  - `emergency_close_all()` não chamava `_insert_history()` → `copytrade_history` vazio
  - Fix: cada posição fechada (ou falha) agora gera registro com `action=EMERGENCY_CLOSE`
  - Inclui `volume` do parsing de posições flattenadas e resolve `master_broker` por role

## CopyTrade - Implementação em Andamento
- [x] Fix JSON serialization bug (flattening nested JSONNode objects)
- [x] Fix cálculo de lotes para Forex (fracionários em vez de inteiros)
- [x] Schema SQLite para rastreamento de posições e status de slaves
- [x] Estrutura base do heartbeat de sincronização
- [x] Detecção de operações alienígenas (estrutura)
- [x] Rastreamento de posições em open_positions (com POSITION_IDENTIFIER)
- [x] GET_POSITIONS no EA com formato flattenado
- [x] Validação NETTING feita ao ATIVAR CopyTrade, não no startup
- [x] GET_ACCOUNT_MODE no EA (auto-detecção de modo da conta)
- [x] cache_detected_mode() + detect_all_account_modes() no Python
- [x] Heartbeat push do EA (não polling do Python) com intervalo configurável
- [x] Guardar Master_ticket → Slave_ticket mapping em open_positions
- [x] Usar ticket específico ao fechar posição (TRADE_POSITION_CLOSE_ID)
- [x] GET_SYMBOL_INFO no EA (VOLUME_MIN/MAX/STEP)
- [x] normalize_volume() com floor/ceil modes (force_min)
- [x] Sell-through prevention (_normalize_and_cap)
- [x] Padrão ouro NETTING: só 3 comandos EA (BUY/SELL/CLOSE_ID)
- [x] PARTIAL_CLOSE convertido para SELL/BUY (elimina TRADE_POSITION_PARTIAL bugado)
- [x] Tracking de direction (BUY/SELL) para distinguir ADD vs REDUCE
- [x] Callbacks diferenciados: _on_open, _on_close, _on_add, _on_partial_close
- [x] Limpeza de código morto (heartbeat, reconcile, alien stubs, validate_account_modes)
- [x] **P2: Detecção de alien via magic number no EA** — DEAL_MAGIC check em OnTradeTransaction ✅
- [x] **P2: Popup de alerta na UI** quando alien detectado (QMessageBox.warning) ✅
- [x] **Magic Number configurável na GUI** — seção CopyTrade na página Configurações ✅
- [x] **Heartbeat interval configurável na GUI** — seção CopyTrade na página Configurações ✅
- [x] **Emergency close com status PANIC** — diferencia de CLOSED normal no DB ✅
- [x] **Emergency close registrado no copytrade_history** — ação EMERGENCY_CLOSE ✅
- ~~**P3: Auto-detecção HEDGE/NETTING**~~ — descartado: operaremos apenas NETTING
- [ ] Retry com validação preço/tempo - max_price_deviation e max_retry_age
- [ ] UI para visualizar status de slaves (ACTIVE/PAUSED com motivo da pausa)
- [ ] UI para botão "Reativar CopyTrade" (100% manual, sem auto-resume)
- [ ] UI para visualizar/alterar account mode (Configurações -> Corretoras)
- [ ] Testes unitários do CopyTrade
- [ ] Documentação do CopyTrade

## Prioridade Alta (Trading / Segurança)
- [x] Tracking de posições fraco — resolvido com POSITION_IDENTIFIER + open_positions DB ✅
- [x] Cálculo de lote ignora mínimo do broker — resolvido com GET_SYMBOL_INFO + normalize_volume ✅
- [ ] HEDGE mode não suportado — tracking assume NETTING (uma posição por símbolo)
- [x] Emergency close não verifica se realmente fechou — limpa estado antes de confirmar ✅
  - Fix: agora marca PANIC no DB, grace period 5s, log de cada close no copytrade_history
- [ ] Senhas em texto puro no brokers.json e nos argumentos de CLI do MT5
- [ ] Sem sanitização de input — path traversal possível pelo nome do broker

## Prioridade Média (Estabilidade / Core)
- [ ] Timeout do ZMQ vaza memória (response events órfãos no zmq_router)
- [ ] Race condition no disconnect/reconnect de brokers
- [ ] Signal emission de thread errada pode crashar o Qt (copytrade_manager)
- [ ] SQLite sem transaction wrapping — estado inconsistente possível
- [ ] SQLite connection nunca é fechada (file locking)
- [ ] Histórico de trades cresce indefinidamente (sem rotação/archive)
- [ ] Sem validação de schema no brokers.json
- [ ] JSON repair hack no zmq_router é frágil — pode esconder erros reais

## Prioridade Baixa (Melhorias / Features)
- [ ] Sem filtro de símbolos no copytrade (bloquear ativos específicos)
- [ ] Sem limite de volume para prevenir trades acidentais grandes
- [ ] Copy trades enviados sequencialmente — lag compensation ausente
- [ ] Sem partial fill handling (assume fill total ou rejeição total)
- [ ] Sem resync automático após queda de conexão
- [ ] Localhost hardcoded no zmq_router — sem suporte a multi-máquina
- [ ] Sem rate limiting nos comandos ZMQ

## Refatoração / Arquitetura
- [ ] Substituir QTimer de polling dos indicadores por signal do mt5_process_monitor
- [ ] Mover trade_allowed_states e connection_status_states de globals para instância
- [ ] Centralizar timeouts e magic numbers em config
- [ ] Adicionar versionamento e migração de config

## GUI (Cosméticos)
- [ ] Ajuste fino da estética geral (refinamento visual)
- [ ] Salvar/restaurar geometria da janela entre sessões
- [ ] Cores dos indicadores de status devem vir do tema (não hardcoded)

