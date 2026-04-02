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

