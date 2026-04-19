# Changelog

Todas as mudanças notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
e este projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

Tipos de mudança:
- **Added** — novas funcionalidades
- **Changed** — mudanças em funcionalidades existentes
- **Deprecated** — funcionalidades que serão removidas
- **Removed** — funcionalidades removidas
- **Fixed** — correções de bugs
- **Security** — correções de vulnerabilidades

---

## [Unreleased]

## [0.1.6] — 2026-04-19

### Changed
- **Emergency close paraleliza slaves**: o master continua sendo fechado sequencialmente (primeiro, para que `_emergency_active=True` suprima replicação redundante), mas os slaves agora são processados em paralelo via `asyncio.gather`. Dentro de cada broker, as posições também são fechadas em paralelo. Em teste com 1 master + 1 slave a sequência era ~3.7s (POSITIONS+CLOSE do master + POSITIONS+CLOSE do slave em série); com múltiplos slaves o ganho escala. Extraídos helpers `_emergency_close_broker` e `_emergency_close_one` para isolar a lógica por broker/posição.
- Bump de versão: `0.1.5` → `0.1.6`

## [0.1.5] — 2026-04-19

### Fixed
- **SQLite sem transaction wrapping em writes multi-statement** (#62): quatro pontos do `copytrade_manager` executavam dois UPDATEs consecutivos e só commitavam no fim. Se o processo crashasse (ou lançasse exceção) entre os statements, a primeira escrita era perdida no rollback implícito, deixando `open_positions` e `master_positions` dessincronizados. Envolvido em `with self.db:` (context manager do sqlite3 → commit em sucesso, rollback em exceção):
  - `handle_master_sltp_update` (open_positions + master_positions)
  - `_track_master_position` em PARTIAL_CLOSE (master + open_positions legacy)
  - `_track_master_position` em CLOSE (master status + open_positions status)
  - `emergency_close_all` (PANIC em open + CLOSED em master)
- **SQLite connection nunca era fechada** (#63): `CopyTradeManager.db` permanecia aberto até a saída do processo. No Windows isso mantinha o arquivo `copytrade_history.db` locked, impedindo backup/delete com o app fechando graciosamente. Adicionado `CopyTradeManager.close()` e chamada em `main.shutdown_cleanup()` após o encerramento dos processos MT5.

### Changed
- Bump de versão: `0.1.4` → `0.1.5`

## [0.1.4] — 2026-04-19

### Changed
- **`TcpMessageHandler` deixou de usar globals de módulo** (#74): `trade_allowed_states` e `connection_status_states` eram dicionários no nível do módulo — vestígio da era ZMQ que impedia múltiplas instâncias e dificultava testes unitários. Agora são atributos de instância (`self._trade_allowed_states`, `self._connection_status_states`). API pública (`get_trade_allowed_states()`, `get_connection_status_states()`, `clear_broker_status()`) permanece inalterada, então os consumidores em `gui/pages/brokers_page.py` e `gui/pages/dashboard_page.py` não precisaram de mudança.
- Renomeado o parâmetro `zid` para `client_id` no loop de identificação de broker em `handle_tcp_message` — última referência nominal ao ZMQ no código Python.
- Bump de versão: `0.1.3` → `0.1.4`

## [0.1.3] — 2026-04-19

### Added
- **Tabela `master_positions` como fonte de verdade do estado do master** (#101): nova tabela SQLite rastreia o estado do master (direction, volume, sl, tp, status) independentemente dos slaves. Resolve dois edge-cases pós-fix #102: (a) master abre com volume tão pequeno que o multiplier do slave dá floor=0 — nenhuma row em `open_positions` era criada, então um ADD subsequente era tratado como abertura fresh e a razão de partial close ficava errada; (b) REVERSAL após floor=0 — Python não conseguia calcular o excess correto sem saber o `prev_vol` do master.

### Changed
- **`_track_master_position`** expandido para cobrir todos os trade_actions (BUY/SELL open, ADD, REVERSAL, PARTIAL_CLOSE, CLOSE); mantém `master_positions` em cada evento. Assinatura ampliada com `master_broker`, `symbol`, `direction`, `sl`, `tp`.
- **`_replicate_to_slave`** agora lê `master_prev_vol` de `master_positions` (via `master_info_before`) em vez de `open_positions.master_volume_current` — elimina o off-by-one que ocorria quando slave nunca abriu ou após zero-crossings.
- **PARTIAL_CLOSE**: lógica corrigida para usar `master_prev_vol` como volume ANTES do parcial (e não como já decrementado), deixando o cálculo de `master_before` e `master_remaining` explícitos e sem ambiguidade.
- **`handle_master_sltp_update`** também atualiza `master_positions.sl/tp` além de `open_positions`.
- **`emergency_close_all`** marca `master_positions` como `CLOSED` além de `open_positions` como `PANIC`.
- Bump de versão: `0.1.2` → `0.1.3`

## [0.1.2] — 2026-04-18

### Fixed
- **Master invertia direção e slave ficava com posição oposta** (#104): quando o master fazia uma ordem contrária com volume maior que a posição atual (cruzando zero em netting), o `POSITION_IDENTIFIER` permanecia estável mas `POSITION_TYPE` invertia — o diff do `OnTrade()` comparava apenas volume e classificava o evento como PARTIAL_CLOSE. Resultado: slave fechava parte da posição na direção antiga em vez de inverter, ficando LONG enquanto master ficava SHORT (e vice-versa). O EA agora compara também `POSITION_TYPE`; ao detectar inversão, emite um TRADE_EVENT sintético com `is_reversal=true` carregando `new_direction`, `new_volume` (excedente na perna nova) e `old_direction`/`old_volume`. O Python processa via fluxo de reversal (close da perna antiga + open na nova) usando diretamente os dados do evento, dispensando inferência do DB — evita o off-by-one do `master_volume_current` após cruzamentos de zero

### Changed
- **EA renomeado**: `mt5_ea/ZmqTraderBridge.mq5` → `mt5_ea/EPCopyFlow2_EA.mq5` (nome antigo era legado da era ZMQ). Recompilar no MetaEditor para gerar `EPCopyFlow2_EA.ex5`
- **Dedup de eventos do master**: em reversal sintético, ambos `order_type` (BUY=0 e SELL=1) são registrados no dedup com mesmo `(position_id, timestamp_mql)` — impede que o evento subsequente do `OnTradeTransaction` (com volume total da ordem) seja reprocessado como ADD ou abertura nova
- Bump de versão: `0.1.1` → `0.1.2`

## [0.1.1] — 2026-04-18

### Fixed
- **Risco do slave maior que o master em partial close** (#102): quando master reduzia para um resto que não dividia exatamente pelo `volume_step` do slave (ex: master SELL 0.10 → 0.01 com multiplier 0.5), o slave ficava com volume proporcionalmente maior que o master. Agora o cálculo usa **floor** para o step e, se o resultado ficar abaixo de `volume_min`, o slave fecha 100%. Garantia: risco relativo do slave ≤ risco relativo do master
- **Reversão de posição não replicada** (#102): quando master invertia direção (ex: SELL 0.01 → BUY 0.11, reversão de 0.10), slave apenas fechava a posição existente sem abrir a oposta. Agora executa reversão em 2 passos (CLOSE + OPEN direção oposta), com volume do novo open = `floor(master_excess × multiplier)` respeitando volume_min/step. Se floor cair abaixo de volume_min, slave fica apenas fechado
- **Histórico `PARTIAL_REVERSAL_FAILED`**: novo status para casos raros em que o passo 2 da reversão (open oposto) falha após o passo 1 (close) ter sucesso — permite diagnóstico

### Changed
- **`calculate_slave_lot`** agora aceita `specs` e retorna `0.0` quando o volume calculado fica abaixo de `volume_min` (antes: forçava para `volume_min`, gerando risco excessivo)
- **`calculate_close_volume`** substitui `calculate_partial_close_lot`: retorna `(close_volume, is_full_close)` para que o chamador saiba se precisa emitir CLOSE total ou PARTIAL_CLOSE
- Bump de versão: `0.1.0` → `0.1.1`

## [0.1.0] — 2026-04-18

### Added
- **Replicação de SL/TP do Master para Slaves**: modificações de Stop Loss e Take Profit em posições do master agora são replicadas automaticamente para os slaves (#92)
- **Detecção de fechamento por SL/TP/SO do broker**: quando uma posição do master fecha por SL/TP ou Stop Out, o sistema detecta via snapshot do `OnTrade()` e replica o fechamento para os slaves
- **Coluna `close_reason` no histórico**: cada registro em `copytrade_history` agora indica o motivo do fechamento (`COPYTRADE`, `BROKER_SLTP`, `EMERGENCY`)
- **Coluna "Motivo" na tela de Histórico**: exibição com labels legíveis ("CopyTrade", "Broker SL/TP/SO", "Emergência")
- **Notification center no topo da janela**: substitui o popup modal para alertas de alien trade
- **Dedup de eventos duplicados**: `OnTrade()` e `OnTradeTransaction()` podem emitir eventos para o mesmo trade; dedup via `(position_id, timestamp_mql, order_type)` com expiração de 10s evita replicação duplicada
- **Verificação pós-falha de CLOSE**: quando o slave responde "posição não encontrada", o sistema consulta `GET_POSITIONS` para confirmar se a posição foi fechada pelo broker e marca o registro como `BROKER_SLTP` em vez de `FAILED`

### Changed
- **Versão centralizada**: `__version__` agora vem de `core/version.py` (fonte única de verdade)
- **Log level de trade failures**: mudou de `ERROR` para `WARNING` em `tcp_message_handler.py`, já que o `copytrade_manager` trata a falha downstream (ex: BROKER_SLTP)
- **SocketRead timeout no EA**: reduzido de 100ms para 1ms para desbloquear a main thread do MT5 (#89)

### Fixed
- **Partial close duplicado**: OnTrade e OnTradeTransaction emitiam eventos separados para o mesmo partial close, fazendo o slave fechar o dobro do volume. Agora deduplicado corretamente
- **Histórico PENDING após BROKER_SLTP**: registros ficavam como `PENDING` indefinidamente quando a posição era fechada pelo broker. Agora são atualizados para `SUCCESS` com motivo `BROKER_SLTP`
- **Ruído de ponto flutuante no volume**: valores como `0.010000000000000002` (oriundos de aritmética float no SQLite) apareciam em logs e histórico, e podiam causar rejeição por volume inválido em alguns brokers. Volume lido do DB agora é arredondado a 8 casas
- **Emergency close sem close_reason**: fechamentos de emergência não marcavam o `close_reason`, ficando vazio no histórico
- **Race condition em dedup de reversão**: dedup key agora inclui `order_type`, evitando que uma reversão legítima (BUY seguido de SELL no mesmo segundo) fosse erroneamente filtrada
- **Magic number filter no snapshot do MASTER**: filtro foi removido — master precisa ver todas as posições para detectar mudanças, independente de magic
- **Orphaned coroutine no shutdown do tcp_router** (#87, #88)
- **Dispatch coroutine criado fora do loop** (#89)
- **Renomeação zmq_message_handler → tcp_message_handler** (#90)

---

## [0.0.1] — 2026-04-13

### Added
- Versão inicial do EPCopyFlow 2.0
- Migração de ZMQ para TCP puro
- Gerenciamento de brokers (master/slave) com GUI PySide6
- CopyTrade básico: abertura, fechamento, partial close, add, reduce
- Tracking de posições via `position_id` (POSITION_IDENTIFIER)
- Detecção de alien trades via magic number
- Histórico persistente em SQLite
- Suporte a modo NETTING
- Dashboard com status dos brokers (MT5/EA/BRK/ALG)
- Conversão automática PARTIAL_CLOSE → SELL/BUY em NETTING
- Normalização de volume conforme specs do símbolo (VOLUME_STEP, VOLUME_MIN, VOLUME_MAX)
- Fechamento de emergência (botão)
- Monitor de processo MT5 (detecta crash e reinicia)
- Monitor de internet (detecta queda de conexão)

[Unreleased]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.6...HEAD
[0.1.6]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/EPFILHO/EPCopyFlow2.0/releases/tag/v0.0.1
