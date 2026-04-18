# EPCopyFlow 2.0

Plataforma de copy trading MT5. Um master replica trades para múltiplos slaves
através de uma ponte Python ↔ MetaTrader 5 via TCP.

---

## O que o projeto faz

- **Orquestra** múltiplas instâncias MT5 (uma por corretora) a partir de um único app
- **Replica trades do master** (abrir, fechar, partial close, modificar SL/TP) para todos os slaves conectados
- **Rastreia posições** por `POSITION_IDENTIFIER` (chave universal do MT5) em SQLite
- **Detecta divergências**: alien trades (sem magic number), fechamentos pelo broker (SL/TP/SO), partial fills
- **Monitora saúde**: processo MT5, conexão com broker, conexão com internet, heartbeat do EA
- **Dashboard** com status em tempo real de cada corretora e histórico de operações

Decisão de design: **apenas modo NETTING** (uma posição por símbolo). HEDGE não é suportado.

---

## Arquitetura

```
┌─────────────────────────┐
│   App Python (PySide6)  │  ← GUI + lógica de copytrade
│   • TcpRouter (asyncio) │
│   • CopyTradeManager    │
│   • BrokerManager       │
│   • MT5ProcessMonitor   │
└───────────▲─────────────┘
            │ TCP (JSON length-prefixed)
            │
┌───────────▼─────────────┐
│  MT5 EA (MQL5)          │  ← uma instância por corretora
│  ZmqTraderBridge.mq5    │     (master ou slave)
│  • Socket cliente TCP   │
│  • OnTrade/OnTradeTrans │
└─────────────────────────┘
```

- **Python = servidor TCP** (`asyncio.start_server`), EA = cliente
- **Framing**: 4 bytes big-endian (tamanho) + payload UTF-8 JSON
- **Uma conexão por broker** (bidirecional)
- Migração de ZMQ para TCP puro foi feita na 0.0.1 (removeu `libzmq.dll`, `pyzmq`, `Zmq.mqh`)

---

## Estrutura de diretórios

```
core/
  version.py             # __version__ (fonte única)
  config_manager.py      # parsing de config.ini e brokers.json
  broker_manager.py      # cadastro master/slave, roles, persistência
  tcp_router.py          # servidor TCP asyncio, roteamento de mensagens
  tcp_message_handler.py # parser de eventos do EA → signals Qt
  copytrade_manager.py   # núcleo da replicação + SQLite tracking
  mt5_process_monitor.py # detecta crash do MT5 e reinicia com backoff

gui/
  main_window.py         # janela principal
  themes.py              # cores e estilos
  brokers_dialog.py      # cadastro/edição de corretoras
  pages/                 # Dashboard, Histórico, Configurações, etc
  widgets/               # broker_card, notification_center, etc

mt5_ea/
  ZmqTraderBridge.mq5    # EA único usado por master e slave

main.py                  # ponto de entrada (QtAsyncio)
config.ini               # timeouts, intervalos, magic number, paths
brokers.json             # cadastro das corretoras (com senhas — ver #57)
CHANGELOG.md             # histórico de versões (Keep a Changelog)
```

---

## Versionamento e Changelog

Versão vive em **`core/version.py`** (`__version__`). É importada em `main.py` para
exibir no splash. Seguimos [SemVer](https://semver.org/lang/pt-BR/):
`MAJOR.MINOR.PATCH`.

**Processo ao finalizar um ciclo de mudanças:**

1. Editar `core/version.py` — bump `PATCH` para fixes, `MINOR` para features,
   `MAJOR` para mudanças incompatíveis
2. Mover as entradas de `[Unreleased]` do `CHANGELOG.md` para a nova versão
   (com data `YYYY-MM-DD`) e atualizar os links no rodapé
3. Commit: `chore: release vX.Y.Z`
4. Criar tag (opcional): `git tag vX.Y.Z && git push --tags`

**Durante o desenvolvimento**, novas entradas vão em `[Unreleased]` agrupadas por
tipo (Added / Changed / Fixed / Removed / Security).

---

## Rastreamento de trabalho

- **Backlog e bugs abertos** → GitHub Issues (`epfilho/epcopyflow2.0`)
- **Histórico do que foi feito** → `CHANGELOG.md`
- **Não usar `TODO.md`** — foi removido para evitar duplicação

Ao fechar uma issue, referenciar o commit/PR que resolveu e adicionar a entrada
correspondente no `CHANGELOG.md` (seção `[Unreleased]`).

---

## Convenções

### Git
- Branch de desenvolvimento: `claude/*` (ex: `claude/copytrade-planning-a6pDw`)
- Commits seguem [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`
- Escopo entre parênteses: `fix(copytrade): ...`, `feat(ea): ...`
- Mensagens em inglês no subject, explicação em português/inglês no body

### Código
- Imports organizados: stdlib → third-party → locais
- Type hints onde agregar clareza (não obrigatório)
- Logs com `logger = logging.getLogger(__name__)` no topo do módulo
- Signals Qt para comunicação GUI ↔ core (nunca chamar GUI direto de coroutines)
- SQLite direto via `self.db.execute(...)` (sem ORM)

### EA (MQL5)
- Arquivo único: `mt5_ea/ZmqTraderBridge.mq5`
- Mesmo EA roda em master e slave (role definida pelo Python via `SET_ROLE`)
- **Recompilar no MetaEditor** após alterar — não há build automático

---

## Como rodar

```bash
python main.py
```

Requisitos principais: `PySide6`, Python 3.11+. MT5 deve estar instalado em
`base_mt5_path` (config.ini). Cada corretora usa uma instância separada em
`.mt5_instances/<BROKER-ACCOUNT>/`.

---

## Pontos de atenção para próximas sessões

- **Issues ativas**: consultar `mcp__github__list_issues` para ver o backlog
- **Branch atual**: verificar `git branch --show-current` — desenvolvimento
  acontece em `claude/*`, não direto na main
- **MT5 só roda em Windows** — testes em Linux são limitados à parte Python
- **Magic number** é a fonte única de autoridade para distinguir trades do
  copytrade vs trades manuais (alien). Nunca alterar com posições abertas
- **Dedup de eventos**: `OnTrade()` e `OnTradeTransaction()` podem emitir para o
  mesmo trade. Dedup em `copytrade_manager.py` usa `(position_id, timestamp_mql, order_type)`
- **NETTING only**: partial close é convertido para SELL/BUY oposto com volume
  proporcional (evita bug do `TRADE_POSITION_PARTIAL` no CTrade)
