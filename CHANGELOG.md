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
- **Aba Logs também não recebe mais respostas OK genéricas (catch-all em `_handle_response`)**: o ramo "else" pegava qualquer resposta OK que não casasse com os prefixos de request_id conhecidos (`ping_`, `get_account_*_`, `positions_`, `orders_`, `trade_*` etc.) e emitia `INFO: Resposta de X: {dict gigante}` pro `LogsPage`. O caso típico era a resposta do `SET_MAGIC_NUMBER` no startup — uma linha enorme com o dict da resposta inteira aparecia pra cada broker que conectava, sem trazer informação útil pro operador. Agora só respostas de **erro** chegam à GUI (preserva alerta de falha real). Resposta OK genérica segue para `logger.debug` no arquivo, se ativado.

### Fixed
- **Reativar detecção de alien trades**: o teste do `git revert ea0b64c`. Após reproduzir o cenário B3 em conta real, a pesquisa em fóruns e a auditoria do código confirmaram que o lag/freeze do MT5 vem da infra das corretoras do grupo XP sob carga B3, não do alien check. Detecção volta a funcionar normalmente — alien trade em SLAVE é capturado em `OnTradeTransaction` no caminho `DEAL_ADD` como antes da v0.1.9 + commits subsequentes.

### Changed
- **`LogsPage` da GUI deixa de receber catch-all de toda mensagem TCP**: `tcp_message_handler.handle_tcp_message` chamava `log_message_received.emit(str(message))` para qualquer evento ≠ TICK/HEARTBEAT — incluía `ACCOUNT_UPDATE` (a cada 2s × N brokers), `TRADE_EVENT` master + slaves, `RESPONSE` de PING etc. Em rajada virava poluição visual + custo de `QTextEdit.append`. Agora só vão pra `LogsPage` os eventos relevantes: `REGISTER`/`UNREGISTER` (com prefixo preservado pra `main_window._handle_tcp_messages` continuar detectando), `ALIEN_TRADE`, e respostas de erro. O resto cai no `logger.debug` (arquivo de log se `log_level=DEBUG`). Eventos com signals dedicados (`trade_event_received`, `account_update_received`, `sltp_modified` etc.) seguem chegando normalmente nos consumidores próprios. `CopyTradeManager.copy_trade_log.emit(...)` continua alimentando o `LogsPage` com cada replicação — visão útil pro usuário operar.
- **`LogsPage.QTextEdit` ganhou `setMaximumBlockCount(1000)`**: evita o widget virar monstro de memória após horas de operação. Linhas mais antigas saem automaticamente quando passa do limite.

### Fixed
- **PARTIAL_CLOSE marcando FAILED quando slave fechou por TP local antes**: cenário B3 onde master e slaves compartilham a mesma cotação do símbolo (ex.: WINM26). TP replicado nos slaves bate ao mesmo tempo que no master; quando o `PARTIAL_CLOSE` do master chega ~100ms depois, a posição do slave já fechou — `TRADE_POSITION_CLOSE_ID` retorna `"Posição não encontrada"` e o histórico ficava com `FAILED` indevido. `_replicate_close` (fechamento total) já tinha esse fallback (verifica via `GET_POSITIONS`, marca `SUCCESS` com `close_reason=BROKER_SLTP`); estendido pra `_send_reduce_command` no caminho `is_full_close=True` (PARTIAL_CLOSE em netting que vira CLOSE_ID inteiro). Partial real (volume reduzido via ordem oposta nova) não precisa do fallback — não usa ticket existente.
- **Abertura perdida em B3 quando `OnTradeTransaction` chega antes do broker confirmar**: em corretoras reais com execução assíncrona, o `OnTradeTransaction` pode disparar com `result.deal=0` E `ORDER_POSITION_ID=0` E sem posição ativa ainda — todas as 3 tentativas de resolver `POSITION_IDENTIFIER` falham e o evento ia perdido (Python descartava com `❌ TRADE_EVENT sem position_id!`). Slaves nunca viam a abertura, e qualquer evento posterior (PARTIAL_CLOSE, CLOSE) batia em "slave sem posição". Solução híbrida: (1) o `OnTradeTransaction` deixa de emitir `TRADE_EVENT` quando `position_id=0` numa abertura — sem barulho, sem evento incompleto; (2) o `OnTrade` snapshot diff (que já detectava fechamentos / partials / SL-TP / reversals via diff de cache vs estado atual) ganha um caminho novo: posição que **apareceu** no novo snapshot mas não estava no cache antigo dispara `EmitSyntheticOpenEvent` com `position_id` resolvido (snapshot é construído depois do broker confirmar — `POSITION_IDENTIFIER` é certeiro). Dedup do Python (`(position_id, timestamp_mql, order_type)`) cobre caso edge de OnTradeTransaction e OnTrade emitirem pra mesma abertura. Latência adicional típica: ~100-300ms (uma rodada de OnTrade após o broker confirmar). `request_response` async pra OrderSendAsync continua funcionando normalmente — só o TRADE_EVENT é postergado.

### Added
- **Push periódico de `ACCOUNT_UPDATE` do EA** (a cada ~2s no `OnTimer`): cada EA agora envia automaticamente `STREAM ACCOUNT_UPDATE` com `balance`, `equity`, `margin`, `free_margin`, `currency`, **`profit` (P/L atual — soma de `POSITION_PROFIT` das posições abertas)** e `positions_count`. Substitui o gap de "rotina paralela alimenta GUI com valores das contas" descrito na premissa original — antes os cards ficavam com saldo e P/L desatualizados, só atualizando esporadicamente em resposta a algum evento. Periodicidade controlada por `kAccountUpdateEvery` (constante no EA — 20 ticks × 100ms = 2s). Lado Python: `TcpMessageHandler.account_update_received(dict)` novo; conectado em `MainWindow._connect_signals` para `dashboard_page.update_account_info` e `brokers_page.update_account_info`. Lado UI: `BrokerCard.update_account_info(data)` novo, atualiza os labels existentes (`balance_label`, `positions_label`, `profit_label`); `update_balance` / `update_positions` antigos preservados (ainda servem respostas sob-demanda).

### Fixed
- **Defesa contra "cards encolhidos" e janelinhas momentâneas durante refresh**: `BrokerCard` agora é criado com `parent=self` (a page) em `dashboard_page._do_refresh_brokers` e `brokers_page._do_refresh_brokers`. Antes, criar `BrokerCard(...)` sem parent fazia o widget nascer top-level por uma fração de segundo (entre `__init__` e `addWidget` que re-parents), o que combinado com bursts de refresh podia gerar janelinhas Qt piscando e em alguns momentos cards mal-dimensionados.

- **Janelas Qt piscando (cards de broker virando top-level brevemente) durante "Conectar Todas"**: cada `REGISTER` de EA disparava `_handle_tcp_messages` no `MainWindow`, que chamava `dashboard_page.refresh_brokers()` + `brokers_page.refresh_brokers()`. Para 9 brokers conectando = ~18 refreshes em 1-2s. Cada refresh fazia `card.setParent(None) + card.deleteLater()` em todos os cards — entre o unparent e o deleteLater (assíncrono), o widget vira top-level visível por uma fração de segundo, daí as "janelinhas" piscando. Duas correções: (1) **debounce** de 50ms em `refresh_brokers` (mesmo padrão do `refresh_stats`), coalescendo a rajada em 1-2 refreshes reais; (2) `widget.hide()` ANTES de `setParent(None)` em todos os destroys (cards, slaves grid, master placeholder), garantindo que o widget não fique visível durante o ciclo de destruição. Também passa `self.master_placeholder.show()` quando reusado.

### Changed
- **Skip antecipado quando `_fetch_symbol_specs` retorna None**: antes, se o slave não tinha o símbolo (ex: forex em conta B3 — XP/CLEAR/GENIAL/RICO não têm EURUSD), o Python ignorava o erro do `GET_SYMBOL_INFO` e seguia tentando enviar o trade, que falhava com `"Símbolo X não disponível no broker"` (resposta do `SymbolSelect` no slave EA). Resultado correto, mas com 3 logs por slave inválido (WARNING specs + INFO COPY + ERROR Falha). Agora `_replicate_to_slave` detecta `symbol_specs is None` e pula direto: registra `SKIPPED: símbolo não disponível no broker` no histórico, emite `SKIP [slave]: SYMBOL indisponível` na GUI, e nem manda o comando — elimina o round-trip de tentar+falhar, e os logs caem de 3 para 2 por slave incompatível.
- **`BrokerManager.connect_broker` / `disconnect_broker` deixaram de bloquear a main thread Qt**: a parte pesada (`subprocess.Popen` no connect — pode levar 100-500ms na chamada `CreateProcessW` do Windows; `process.terminate()` + `process.wait(timeout=5)` no disconnect — até 5s se MT5 ignora o sinal) ia direto na thread Qt e congelava a GUI durante a operação. "Conectar Todas" com 9 brokers chegava a 1-4s, "Desconectar Todas" no shutdown podia chegar a N×5s. Agora os métodos públicos: (1) fazem só validações rápidas, (2) marcam o estado em memória otimisticamente, (3) submetem coroutines ao engine via `engine.submit(...)` que rodam o trabalho síncrono em `loop.run_in_executor(None, ...)`, (4) emitem `brokers_updated` pra UI re-renderizar. Bônus: "Conectar Todas" virou paralelo (todas as N coroutines submetidas concorrem no engine, com Popen em executor threads), em vez de sequencial. Falhas no engine logam erro e revertem o estado pro broker, com nova emissão do signal pra UI atualizar.

### Added
- **EA: `SymbolSelect(symbol, true)` defensivo nos handlers de trade**: cenário B3 — operador entra em `WINQ25`, contrato vira `WINV25` no vencimento, slave nunca operou o novo contrato e `OrderSendAsync` falharia com erro genérico. Agora `HandleTradeBuyCommand`, `HandleTradeSellCommand` e `HandleTradePositionCloseSymbolCommand` chamam `SymbolSelect(symbol, true)` no topo: verifica se o símbolo existe no broker e adiciona ao Market Watch automaticamente. Se o broker não tem o símbolo (ainda não disponibilizado, typo, etc.), responde ERROR claro: `"Símbolo X não disponível no broker"`. Custo nulo quando símbolo já está no Market Watch (~µs). Handlers que operam por ticket (`Partial`, `CloseId`) não foram alterados — derivam o símbolo da posição existente.

### Changed
- **GUI lê estado do processo MT5 via `MT5ProcessMonitor.is_running()`, não mais via `process.poll()` direto**: `dashboard_page.update_broker_indicators` e `brokers_page.update_broker_indicators` rodavam num QTimer de 2s e cada um chamava `self.broker_manager.mt5_processes.get(key)` + `process.poll()` por broker, sem segurar o `_state_lock` que o resto do código usa pra acessar o dict. Triplicava o trabalho do watchdog (que já faz `process.poll()` a cada 5s) e violava a thread-safety. Agora o `MT5ProcessMonitor` mantém um cache `_is_running: dict[str, bool]` (atualizado em cada `check_and_restart_processes`, com lock próprio) e expõe `is_running(key) -> bool`. A GUI consulta esse cache em vez de fazer syscall direto. `DashboardPage` ganhou parâmetro `mt5_monitor` no construtor (já existia em `BrokersPage`); `MainWindow` injeta. Trade-off conhecido: latência máxima de ~5s pra GUI ver "MT5 morreu" — antes era ~2s, mas a GUI agora está consistente com o resto do app que já delegava ao monitor.
- **EA: `InpTimerIntervalMs` / `InpTcpHost` / `InpConnectTimeoutMs` viraram `const`** (eram `input`): `input` em MQL5 persiste o valor por chart, e recompilar o `.ex5` mantém o valor antigo do chart — log de produção mostrou inconsistência, com conexões alternando entre 100ms e 1000ms efetivos no mesmo dia. Como esses três não têm caso de uso pra variar por instância (timer ideal é único, host é sempre localhost no fluxo, timeout é fixo), virou `const` no código. O MT5 não oferece mais esses parâmetros na janela de inputs; recompilar = valor novo aplica imediatamente em todas as instâncias. `InpDebugLog` continua `input` (toggle por instância faz sentido pra debug pontual).
- **EA: `OnInit` passou a logar `TimerInterval=Xms`** na linha de inicialização — permite verificar pelo painel "Experts" do MT5 qual valor está efetivamente rodando, sem adivinhar pelas latências.

### Removed
- **`GET_ACCOUNT_MODE` command flow**: na inicialização, `detect_all_account_modes` (em `main.py`) percorria os brokers conectados e fazia round-trip ao EA pra perguntar qual o modo da conta, gravando o resultado em `brokers.json`. Como o sistema é **NETTING-only** por design (`validate_broker_for_copytrade` já bloqueia qualquer outra coisa) e o `mode` lido pela `validate_*` vem do `brokers.json` cadastrado pelo usuário (com fallback `"Netting"`), a detecção dinâmica era redundante. Removidos: `CopyTradeManager.detect_and_cache_account_mode`, `CopyTradeManager.detect_all_account_modes`, `BrokerManager.cache_detected_mode`, `HandleGetAccountModeCommand` no EA e o case no dispatcher. -88 linhas, zero referências órfãs. `BrokerManager.get_account_mode` permanece — usado por `validate_broker_for_copytrade`.

### Changed (cont.)
- **EA `InpTimerIntervalMs`: 1000ms → 100ms**: o `OnTimer()` do EA é onde os comandos vindos do Python são lidos do socket TCP (`CheckIncomingCommands` → `TcpPumpReads` → `TcpExtractAndProcessFrames`). Em 1000ms, um comando que chegasse logo após um tick esperaria até 1s para ser processado — causando gap medido de ~1.4s entre execução do trade no master e no slave (master 213ms broker + ~1s timer slave + 258ms broker slave). Em 100ms, latência max do timer cai para 100ms; custo extra é insignificante (conteúdo do `OnTimer` é trivial — checks de flag e drain de socket vazio). **Requer recompilar o EA no MetaEditor (F7) e re-attach nas instâncias.**

## [0.1.9] — 2026-05-02

### Fixed
- **Dashboard não atualizava stat cards após trade replicado** (#111, follow-up PR 3): bug pré-existente exposto pelo smoke test do PR 3. `dashboard_page._update_copytrade_stats()` só era chamado em startup, theme change ou broker connect/disconnect — nunca em resposta a `copy_trade_executed`/`copy_trade_failed`. Histórico já tinha esse wire (linhas 266-267 de `main_window.py`); dashboard ficou de fora desde sempre. Adicionado `refresh_stats(_data=None)` como Slot público em `dashboard_page.py` e conectado os 2 sinais em `main_window.py`. Agora cada trade replicado dispara um refresh dos cards Total/Sucesso/Falha. Não tem custo extra — `request_today_stats` é fire-and-forget no motor.
- **EcoQoS / Power Throttling do Windows não era desligado pelos processos MT5** (#111, PR 2.6): teste em conta REAL (B3) com 7 MT5s mostrou que `HIGH_PRIORITY_CLASS` do PR 2.5 não bastou — usuário relatou freeze ao alternar janelas e lentidão no painel "Negociação" do próprio MT5 (não na nossa GUI). Pesquisa confirmou: priority class e EcoQoS são ortogonais na Microsoft API. Mesmo com prioridade alta, Windows pode marcar processo em background como "Eco" e reduzir CPU/IO — efeito agravado em real (mais ticks, mais book) versus demo (sintético). Adicionado `core/win_process.py` com helper `disable_power_throttling(pid)` que chama `SetProcessInformation` via `ctypes` com `ProcessPowerThrottling` + `PROCESS_POWER_THROTTLING_EXECUTION_SPEED`, `StateMask=0` (desligado). Wired após `subprocess.Popen` em `core/broker_manager.py::connect_broker` e `core/mt5_process_monitor.py::restart_mt5_instance`. Falha silenciosa com log de warning se a API estiver indisponível (Windows < 1709) ou OpenProcess falhar — não derruba o app. Não-Windows: no-op. Doc Microsoft: https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-process_power_throttling_state
- **`UnicodeEncodeError` no console do Windows com logs contendo emoji** (#111, PR B): no Windows, `sys.stdout` default é `cp1252`. Várias mensagens de log em `core/copytrade_manager.py` carregam emojis (✅ ❌ ⚠️) — qualquer um deles dispara `UnicodeEncodeError` no `StreamHandler` e silencia o handler dali em diante. Adicionada reconfiguração defensiva em `main.py::setup_logging` (antes de anexar handlers): `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` para `stdout` e `stderr`, com `hasattr` protegendo contra streams redirecionados que não suportam `reconfigure`. `errors="replace"` evita crash em caracteres exóticos — substitui por `?`. File handler já estava com `encoding="utf-8"`; só o stream do console precisava do fix.
- **Throttle do Windows nos processos MT5 durante Alt+Tab** (#111, PR 2.5): após o PR 2 (separação GUI/motor em threads), teste em demo na B3 com 1 master + 7 slaves ainda apresentou lag de 10–25s na replicação ao mudar de janela. Captura de log mostrou que dois slaves recebiam o mesmo `TRADE_POSITION_CLOSE_ID` no mesmo milissegundo: um respondia em 1ms, o outro em 25.700ms (timeout do Python ocorria em 5s; resposta tardia chegava ~20s depois com `retcode: 10009`). A diferença não está no código Python — é o **scheduler do Windows throttlando processos MT5 que perderam foco**. Fix: passar `creationflags=subprocess.HIGH_PRIORITY_CLASS` nos `subprocess.Popen` que iniciam (`core/broker_manager.py::connect_broker`) e reiniciam (`core/mt5_process_monitor.py::restart_mt5_instance`) o terminal MT5 no Windows. Não usamos `REALTIME_PRIORITY_CLASS` (pode travar o sistema). Linux/macOS: nenhuma mudança — MT5 não roda lá no fluxo do projeto.
- **Dívida técnica registrada (não corrigida neste PR)**: como o Python loga falha por timeout enquanto o broker pode confirmar a operação tardiamente (ex.: trade do XP foi executado com `retcode: 10009` mas o registro local já tinha sido marcado como falha), o DB do copytrade pode ficar inconsistente com a realidade do broker em cenários de throttle severo. Natureza similar (mas independente) do que motivou #56. Tratamento adequado fica para issue futura — depende de medirmos se o `HIGH_PRIORITY_CLASS` por si só já elimina o cenário em produção real.

### Changed
- **Logs reduzidos em `copytrade_manager.py`**: pente fino tirando 14 logs `DEBUG` redundantes (eventos descartados, dedup, prints internos de `_track_master_position` e `_on_*_success`) e 13 `INFO` que duplicavam informação já presente no log canônico do mesmo fluxo (recebimento, classificação, dump completo de respostas em `_replicate_close`/`_send_*_command` e nas duas etapas do REVERSAL). Total: 114 → 88 logs. `copy_trade_log.emit(...)` (alimenta a `LogsPage` da GUI) e todos `warning`/`error`/`exception` preservados.
- **`config.ini`**: `log_level` agora `INFO` por padrão (era `DEBUG`); `monitor_interval` baixado de `1s` para `5s` — o watchdog do MT5 conferia processo a cada segundo, exagero para detecção de crash; 5s mantém o objetivo com 5× menos overhead.
- **`MainWindow.version_label`**: passa a usar `core.version.__version__` em vez do `"v0.0.1"` hardcoded, que estava desatualizado desde a 0.1.0.
- **Limpeza geral de código pós-refactor** (#111, follow-up): aplicada após
  review automatizado dos PRs 2.5/2.6/B/3. Mudanças:
  - **Índice em `copytrade_history(timestamp)`** (`_init_db`): `_fetch_today_stats`
    e `_fetch_trade_history` faziam full scan; com a tabela crescendo, o WHERE
    timestamp + ORDER BY DESC ficavam lentos. `CREATE INDEX IF NOT EXISTS` no
    init resolve.
  - **Debounce de 200ms em `dashboard_page.refresh_stats`**: cada
    `copy_trade_executed`/`copy_trade_failed` disparava `request_today_stats`.
    Em real-B3 com burst de trades isso geraria múltiplas queries por segundo.
    Coalesce via `QTimer.singleShot(200)` → max 1 refresh por janela de 200ms.
  - **Remoção de `sys.platform.startswith("win")` redundante** em
    `broker_manager.py::connect_broker` e `mt5_process_monitor.py::restart_mt5_instance`:
    `disable_power_throttling` já checa internamente e retorna False fora do
    Windows. Também removido `import sys` órfão de `mt5_process_monitor.py`.
  - **Refator de `CopyTradeManager.close()`**: extraído `_do_close()` como
    helper síncrono compartilhado entre o caminho com engine (via
    `engine.submit(_async_close)` + `result(timeout=2.0)`) e o caminho sem
    engine (test environment). Elimina duplicação do `try/except/finally` que
    existia em 2 lugares.
  - **Round defensivo no log de ADD ok**: linha
    `📊 ADD ok: +0.05 (slave: 0.1 → 0.15000000000000002)` (ruído de aritmética
    float em SQLite/Python) virou `slave: 0.1 → 0.15`. Cosmético.
  - **Comentários enxugados**: removidas referências "PR X troca por Y", "issue
    #111", anedotas tipo "respostas demorando 25s+", e Slot docstrings óbvios
    ("roda na main thread") em `copytrade_manager.py`, `broker_manager.py`,
    `mt5_process_monitor.py`, `win_process.py`, `history_page.py`,
    `dashboard_page.py`, `notification_center.py`, `main_window.py`. O CHANGELOG
    e o histórico git já documentam o "porquê" histórico — código mantém só o
    "porquê" intemporal.

- **SQLite confinado ao thread do motor** (#111, PR 3): fim da solução intermediária do PR 2. `CopyTradeManager` agora usa conexão SQLite com `check_same_thread` default (sem flag de bypass) e **sem locks** — engine asyncio é single-threaded, então não há contenção possível. Leituras pedidas pela GUI seguem padrão **request + fetch async + signal**: `request_trade_history()` / `request_today_stats()` (sync, qualquer thread) submetem coroutine ao motor via `engine.submit()`; coroutines `_fetch_trade_history` / `_fetch_today_stats` rodam no motor, fazem a query e emitem 2 sinais novos (`trade_history_ready(list, str)`, `today_stats_ready(dict)`) via cross-thread queued connection — slots na main thread atualizam UI quando o resultado chega. `close()` virou wrapper que submete `_async_close` ao motor (com `result(timeout=2.0)`) — garante que o `db.close()` aconteça na thread certa antes do `engine.stop()`. Wire de `copytrade_manager.engine` feito em `main.py` após o bootstrap (mesmo padrão do `tcp_message_handler`). `gui/pages/history_page.py` e `gui/pages/dashboard_page.py` adaptados: conectam aos sinais no `__init__` e disparam `request_*()` em vez do antigo `get_*()` síncrono. Resultado: zero risco de contenção SQLite entre motor e GUI; código também fica mais simples (não precisa mais de `threading` import em `copytrade_manager.py`).
- **Bootstrap separado em GUI thread (Qt) e motor thread (asyncio)** (#111, PR 2): mudança arquitetural atômica. `main.py` reescrito: removida dependência de `PySide6.QtAsyncio`/`qasync`, `app.exec()` padrão volta a ser o event loop da main thread. Um `EngineThread` dedicado hospeda o loop do motor — `TcpRouter`, `CopyTradeManager` e `TcpMessageHandler` são construídos **dentro** desse thread (via coroutine de bootstrap submetida à engine), garantindo thread affinity Qt correta dos QObjects emissores. Isso resolve o **freeze de replicação durante Alt+Tab** observado em produção (B3): com a única main thread, Windows throttlava CPU do motor junto com a GUI; agora o motor compete por CPU em pé de igualdade com a GUI dentro do processo.
- **`BrokerManager`**: recebe parâmetro `engine` no construtor. Os 3 sites que usavam `asyncio.create_task(...)` em `connect_broker`/`disconnect_broker` (chamados da main thread via botões) viraram `engine.submit(...)`. Adicionado `threading.RLock` (`_state_lock`) protegendo `connected_brokers` e `mt5_processes`. Novos acessores thread-safe: `set_mt5_process`, `set_connected`, `get_mt5_process`.
- **`TcpMessageHandler`**: recebe parâmetro `engine`. `send_ping`/`send_get_status_info` (slots de botões) viraram `engine.submit`. `threading.Lock` protege `_trade_allowed_states` e `_connection_status_states` (escritos pelo motor, lidos por QTimer da GUI a cada 2s).
- **`CopyTradeManager`**: agora construído dentro do bootstrap do motor (sqlite connection nasce na thread do motor). PR 2 abriu uma janela intermediária com `check_same_thread=False` + `threading.Lock` para as 2 leituras síncronas da GUI; PR 3 (abaixo) confina o DB inteiramente ao motor via signals.
- **`MT5ProcessMonitor`**: zero mudanças semânticas; passa a receber `engine.loop` em vez do loop unificado e usa os novos acessores thread-safe do `BrokerManager` em vez de mexer nos dicts diretamente.
- **`MainWindow`**: construtor recebe `tcp_message_handler` e `engine` em vez de construir o handler internamente. Botão de emergência usa `engine.submit(emergency_close_all())`. `closeEvent` reescrito como sequência ordenada: para timers/monitores → desconecta brokers → para `MT5ProcessMonitor` → submete `tcp_router.stop()` ao motor (com `result(timeout=5)`) → fecha `CopyTradeManager` → `engine.stop(timeout=5)`.
- **Splash screen**: convertido de `await asyncio.sleep` para `QTimer.singleShot`.
- **Sinal SIGINT**: callback agora chama `QApplication.instance().quit()` (delega teardown ao `closeEvent`).
- **Removido**: `shutdown_event` (`asyncio.Event`) e `shutdown_cleanup()` do `main.py` — orquestração migrou para `closeEvent`.

### Added
- **`core/engine_thread.py`** (#111, PR 1): infraestrutura `EngineThread` para hospedar o event loop do motor de trade em uma thread daemon dedicada, isolada da main thread (Qt). API mínima: `start()` (bloqueia até loop pronto via `threading.Event`), `submit(coro) -> concurrent.futures.Future` (wrapper sobre `asyncio.run_coroutine_threadsafe`), `stop(timeout)` (cancela tasks pendentes, para o loop, faz join). Inclui `loop.set_exception_handler` para que exceções em coroutines/tasks não derrubem o loop. Acompanhada de `tests/test_engine_thread.py` (13 testes unitários, stdlib `unittest`).

## [0.1.8] — 2026-04-23

### Fixed
- **`position_id=0` em conta real com execução assíncrona na B3** (#109): em corretoras reais, `OrderSend` retorna com `result.deal=0` (o deal é confirmado assincronamente pela bolsa). As duas tentativas existentes de derivar o `POSITION_IDENTIFIER` (`HistoryDealSelect` e `PositionSelect`) falhavam nesse cenário, fazendo o Python rejeitar o TRADE_EVENT com `❌ TRADE_EVENT sem position_id!` e não replicar a abertura para os slaves. Adicionada **3ª tentativa** via `HistoryOrderGetInteger(result.order, ORDER_POSITION_ID)`: a ordem já existe no histórico com `ORDER_POSITION_ID` preenchido mesmo antes do deal ser confirmado. Fix cirúrgico no EA (`EPCopyFlow2_EA.mq5`); comportamento em conta demo inalterado.
- Bump de versão: `0.1.7` → `0.1.8`

## [0.1.7] — 2026-04-19

### Changed
- **Emergency close Option C** (#56): reescrita completa de `emergency_close_all`.
  - **Fase 1** — close direto por ticket sem round-trip de POSITIONS: lê `master_positions` e `open_positions` do DB e dispara todos os closes (master + todos os slaves) em paralelo via `asyncio.gather`. Elimina ~1–2s de overhead por POSITIONS desnecessário e remove a serialização master-primeiro/slaves-depois.
  - **Fase 2** — reconciliação: GET_POSITIONS em cada broker após fase 1 para detectar e fechar posições órfãs (não rastreadas no DB ou cujo close falhou silenciosamente). Fecha órfãs também em paralelo. Resolve #56.
  - Helper `_emergency_close_broker` removido (substituído pelo novo fluxo). `_emergency_close_one` mantido e reaproveitado pelas duas fases.
- Bump de versão: `0.1.6` → `0.1.7`

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

[Unreleased]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.9...HEAD
[0.1.9]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/EPFILHO/EPCopyFlow2.0/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/EPFILHO/EPCopyFlow2.0/releases/tag/v0.0.1
