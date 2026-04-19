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

[Unreleased]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/EPFILHO/EPCopyFlow2.0/releases/tag/v0.0.1
