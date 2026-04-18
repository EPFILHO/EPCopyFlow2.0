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

### Changed
- **EA renomeado**: `mt5_ea/ZmqTraderBridge.mq5` → `mt5_ea/EPCopyFlow2_EA.mq5` (nome antigo era legado da era ZMQ). Recompilar no MetaEditor para gerar `EPCopyFlow2_EA.ex5`

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

[Unreleased]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/EPFILHO/EPCopyFlow2.0/releases/tag/v0.0.1
