# EPCopyFlow 2.0 - TODO

## Prioridade CRÍTICA (Bugs encontrados em teste)
- [ ] **CRASH LOOP** — Process monitor reinicia MT5 infinitamente se ele fechar logo após iniciar
  - MT5 inicia, morre em <5s, reinicia, morre de novo — loop infinito por 2+ minutos
  - Solução: adicionar max retry count (ex: 3 tentativas), backoff exponencial (1s, 2s, 4s)
  - Após N falhas, parar de reiniciar e notificar usuário em popup/log
  - Arquivo: `core/mt5_process_monitor.py` — método `restart_mt5_instance()`
  
- [ ] **MT5 CARDS CINZA** — Indicadores ficam cinza com MT5 aberto porque processo morre rapidamente
  - Causado pelo crash loop acima — processo sobe/desce a cada 5s, QTimer vê processo morto
  - Será resolvido automaticamente quando crash loop for corrigido

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

- [ ] **ALIEN OPERATIONS NÃO DETECTADAS (P2)** — Ordem aberta manualmente no Slave não é detectada
  - Python deveria detectar (não tem ticket no open_positions) e pausar CopyTrade
  - Causa: heartbeat é enviado pelo EA mas Python não processa para detecção
  - Falta: implementação de `_detect_alien_operations()` que comparar posições Slave vs BD
  - **PRÓXIMO PASSO**

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
- [ ] **P2: Implementar _detect_alien_operations()** — comparar heartbeat Slave vs open_positions ← PRÓXIMO
- [ ] **P2: Pausar CopyTrade automaticamente** quando alien detectado (com mensagem clara)
- [ ] **P3: Auto-detecção HEDGE/NETTING** — adaptar fluxo conforme modo da conta
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
- [ ] Emergency close não verifica se realmente fechou — limpa estado antes de confirmar
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

