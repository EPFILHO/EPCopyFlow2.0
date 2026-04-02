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
  - Faltava master_ticket no tuple de valores
  - Arquivo: `core/copytrade_manager.py` linha 361 em `_track_master_position()`
  - Commit: `0b467ca`

- [ ] **FECHAMENTO PARCIAL FECHA TUDO** — Fechar 1.0 de 2.0 no Master fecha TODAS posições no Slave
  - Master: 2.0 lotes abertos → fecha parcial 1.0 (1.0 restante)
  - Python envia: `TRADE_POSITION_CLOSE` com symbol=USDJPY (sem ticket)
  - Slave: fecha TODAS posições USDJPY (inclusive a alienígena 0.01)
  - Causa: falta de mapeamento Master_ticket → Slave_ticket na BD
  - Solução: guardar mapping no open_positions, enviar comando com ticket específico

- [ ] **ALIEN OPERATIONS NÃO DETECTADAS** — Ordem aberta no Slave não foi pausada
  - Python deveria detectar (não tem ticket no open_positions) e pausar
  - Causa: heartbeat é enviado pelo EA mas Python não processa para detecção
  - Falta: implementação de `_detect_alien_operations()` que comparar posições Slave vs BD

## CopyTrade - Implementação em Andamento
- [x] Fix JSON serialization bug (flattening nested JSONNode objects)
- [x] Fix cálculo de lotes para Forex (fracionários em vez de inteiros)
- [x] Schema SQLite para rastreamento de posições e status de slaves
- [x] Estrutura base do heartbeat de sincronização
- [x] Detecção de operações alienígenas (estrutura)
- [x] Rastreamento de posições em open_positions
- [x] GET_POSITIONS no EA com formato flattenado
- [x] Validação NETTING feita ao ATIVAR CopyTrade, não no startup
- [x] GET_ACCOUNT_MODE no EA (auto-detecção de modo da conta)
- [x] cache_detected_mode() + detect_all_account_modes() no Python
- [x] Heartbeat push do EA (não polling do Python) com intervalo configurável
- [x] **FIX IMEDIATO:** SQL binding error em _track_master_position() ✅ FIXADO
- [ ] **FIX IMEDIATO:** Guardar Master_ticket → Slave_ticket mapping em open_positions
- [ ] **FIX IMEDIATO:** Usar ticket específico ao fechar posição (não symbol)
- [ ] Implementar _detect_alien_operations() - comparar heartbeat Slave vs open_positions
- [ ] Pausar CopyTrade automaticamente quando alien detectado (com mensagem clara)
- [ ] Atualizar open_positions quando posição fecha - marcar status CLOSED quando Slave confirma
- [ ] Retry com validação preço/tempo - max_price_deviation e max_retry_age (skeleton em config.ini)
- [ ] UI para visualizar status de slaves (ACTIVE/PAUSED com motivo da pausa)
- [ ] UI para botão "Reativar CopyTrade" (100% manual, sem auto-resume)
- [ ] UI para visualizar/alterar account mode (Configurações -> Corretoras)
- [ ] Testes unitários do CopyTrade
- [ ] Documentação do CopyTrade
- [ ] Melhorias em logging/auditoria do CopyTrade

## Prioridade Alta (Trading / Segurança)
- [ ] Tracking de posições fraco — no modo hedge pode fechar posição errada no slave
- [ ] Sem validação de parâmetros de trade — ordens inválidas cascateiam para todos os slaves
- [ ] Cálculo de lote ignora mínimo do broker — ordens rejeitadas silenciosamente
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

