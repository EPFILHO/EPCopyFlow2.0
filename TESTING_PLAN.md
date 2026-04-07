# EPCopyFlow 2.0 — Plano de Testes Exaustivos

## Objetivo
Validar toda a pipeline de copytrade em ambiente real com múltiplos EAs (Master + Slaves), garantindo rastreamento correto, validação de volume, detecção de aliens e resiliência.

---

## 1. TESTES DE FLUXO PRINCIPAL (Trade Replication)

### 1.1 Abertura Simples (NETTING)
**Cenário**: Master abre BUY EURUSD 1.0 lot → todos os slaves replicam proporcionalmente

**Setup**:
- Master: NETTING mode, EURUSD aberto
- Slaves: CLEAR, FBS, FOTMARKETS (todos NETTING)
- Multipliers: 1.0x, 0.5x, 0.3x respectivamente

**Steps**:
1. Master abre BUY EURUSD 1.0 lot via MT5 ou EA
2. Aguardar 2-3s (latência ZMQ)
3. Verificar na UI Dashboard: "Total Copias" incrementou
4. Verificar em cada slave no MT5: posição aberta com volume correto
   - CLEAR: 1.0 lot (1.0 × 1.0x)
   - FBS: 0.5 lot (1.0 × 0.5x)
   - FOTMARKETS: 0.3 lot (1.0 × 0.3x)
5. Verificar Historico page: 3 linhas (1 master → 3 slaves) com status SUCCESS
6. Verificar DB copytrade_history e open_positions:
   - master_ticket (POSITION_IDENTIFIER) está em todas as 3 rows
   - slave_volume_current correto
   - direction = BUY em todas

**Validação Técnica**:
- Logs: "MASTER [master_key]: BUY EURUSD 1.0 lotes"
- Logs: "COPY [slave_key]: BUY EURUSD X.X lotes" (3x)
- Sem erros de volume (normalize_volume deve aceitar)

**Pass Criteria**: ✅ Todas as 3 posições abertas, volumes corretos, DB consistente

---

### 1.2 Fechamento Total (NETTING)
**Cenário**: Master fecha BUY EURUSD 1.0 lot → todos os slaves fecham

**Setup**: Continuação do teste 1.1 (posições abertas)

**Steps**:
1. Master fecha BUY EURUSD 1.0 lot (CLOSE via MT5)
2. Aguardar 2-3s
3. Verificar em cada slave: posição FECHADA (não aparece mais em Positions)
4. Verificar Dashboard: "Total Copias" não muda (métrica cumulativa), mas cards atualizam
5. Verificar Historico: 3 linhas novas com action=CLOSE, status=SUCCESS
6. Verificar DB open_positions: todas as 3 rows agora status=CLOSED

**Validação Técnica**:
- Logs: "MASTER [master_key]: CLOSE EURUSD (volume_remaining=0)"
- Logs: "COPY [slave_key]: CLOSE EURUSD via TRADE_POSITION_CLOSE_ID ticket=X"
- Sem erros "sell-through" (volume negativo)

**Pass Criteria**: ✅ Todas as 3 posições fechadas, status CLOSED no DB

---

### 1.3 Fechamento Parcial (NETTING)
**Cenário**: Master fecha PARCIALMENTE (reduz volume) → slaves reduzem proporcionalmente

**Setup**:
1. Master abre BUY EURUSD 2.0 lots
2. Aguardar replicação (testes 1.1)
3. Master fecha parcialmente 1.0 lot (volume restante = 1.0)

**Steps**:
1. Master executa PARTIAL_CLOSE 1.0 lot
2. Aguardar 2-3s
3. Verificar em cada slave: volume reduzido mas posição ABERTA
   - CLEAR: antes 2.0, depois 1.0
   - FBS: antes 1.0, depois 0.5
   - FOTMARKETS: antes 0.6, depois 0.3
4. Verificar Historico: 3 linhas novas com action=PARTIAL_CLOSE
5. Verificar DB open_positions: slave_volume_current atualizado, status ainda OPEN

**Validação Técnica**:
- Logs: "master_volume_current -= 1.0" no DB
- Logs: "COPY [slave_key]: PARTIAL_CLOSE→TRADE_ORDER_TYPE_SELL vol=0.5"
  - (convert PARTIAL to opposite BUY/SELL em NETTING)
- normalize_volume com force_min=True (garante mínimo)

**Pass Criteria**: ✅ Volumes corretos, posições ainda abertas, cálculo proporcional validado

---

### 1.4 ADD à Posição (NETTING)
**Cenário**: Master abre BUY, depois abre MAIS BUY (mesmo símbolo) → slaves ADD

**Setup**:
1. Master abre BUY EURUSD 1.0 lot
2. Aguardar replicação
3. Master abre MAIS BUY EURUSD 0.5 lot (mesma posição POSITION_IDENTIFIER muda)

**Steps**:
1. Master executa segunda BUY EURUSD 0.5 lot
2. Aguardar 2-3s
3. Verificar em cada slave: POSITION_IDENTIFIER NEW (diferente da anterior)
   - CLEAR: 1.0 + 0.5 = 1.5 lots (BUY)
   - FBS: 0.5 + 0.25 = 0.75 lots
   - FOTMARKETS: 0.3 + 0.15 = 0.45 lots
4. Verificar Historico: 3 linhas novas com action=BUY (e is_add=True internamente)
5. Verificar DB open_positions: NOVO position_id inserido, ou master_volume_current incrementado?
   - **IMPORTANTE**: Verificar se DB tem múltiplas rows por position_id (uma por slave)
   - ou se faz update na existente

**Validação Técnica**:
- EA deve detectar: posição existente + mesma direção (BUY + BUY) → ADD
- Logs: "NETTING ADD: slave tem 1.0 BUY, adicionando BUY 0.5"
- master_volume_current em open_positions incrementa

**Pass Criteria**: ✅ ADD detectado, volumes somam, DB reflete ADDs

---

### 1.5 REDUCE (NETTING)
**Cenário**: Master tem BUY, depois SELL (direção oposta) → slaves REDUCE

**Setup**:
1. Master abre BUY EURUSD 2.0 lots
2. Aguardar replicação
3. Master abre SELL EURUSD 0.5 lot (contrária)

**Steps**:
1. Master executa SELL EURUSD 0.5 lot
2. Aguardar 2-3s
3. Verificar em cada slave: BUY volume reduzido (1.5, 0.75, 0.45)
   - **NOT** uma posição SELL nova — em NETTING deve fechar parcialmente o BUY
4. Verificar Historico: action=SELL com comentário "CT:{position_id}"
5. Verificar DB: slave_volume_current decrementado, direction ainda BUY

**Validação Técnica**:
- EA detecta: posição BUY + SELL direção oposta → REDUCE
- normalize_volume force_min=True (não deixa ficar < mínimo)
- sell-through prevention: não fecha mais do que slave tem

**Pass Criteria**: ✅ REDUCE detectado, posição BUY reduzida (não flipa)

---

### 1.6 Position Flipping (NETTING)
**Cenário**: Master tem BUY 1.0, depois SELL 2.0 → posição flipa para SELL

**Setup**:
1. Master abre BUY EURUSD 1.0 lot
2. Aguardar replicação
3. Master abre SELL EURUSD 2.0 lots (fecha BUY + abre SELL)

**Steps**:
1. Master executa SELL 2.0
2. Aguardar 2-3s
3. Verificar no MT5 Master: posição é agora SELL 1.0 (BUY fechado)
4. Verificar em cada slave: posição é agora SELL (volume calculado)
5. Verificar DB open_positions:
   - Old position_id com BUY marcado CLOSED
   - NEW position_id com SELL marcado OPEN
6. Verificar Historico: 2 operações (CLOSE do BUY, BUY/SELL da flip)

**Validação Técnica**:
- POSITION_IDENTIFIER muda quando posição flipa
- EA deve enviar 2 TRADE_EVENT's: um CLOSE, um BUY/SELL
- Python processa ambos corretamente

**Pass Criteria**: ✅ Flip detectado, posições corretas em master e todos os slaves, DB reflete mudança

---

## 2. TESTES DE VALIDAÇÃO DE VOLUME

### 2.1 Volume Inválido (< Mínimo do Símbolo)
**Cenário**: Multiplier causa volume < VOLUME_MIN → operação CANCELADA

**Setup**:
- Master: EURUSD com VOLUME_MIN = 0.01
- Slave FBS: multiplier = 0.001 (causaria 0.001 < 0.01)

**Steps**:
1. Master abre BUY EURUSD 0.05 lot
2. FBS deveria replicar com 0.05 × 0.001 = 0.00005
3. Verificar Historico FBS: status=SKIPPED, erro="volume < mínimo do símbolo"
4. Verificar Logs: "❌ Volume inválido para EURUSD"
5. Verificar DB open_positions: FBS não tem entry para essa position_id (ou status=SKIPPED)

**Validação Técnica**:
- normalize_volume com force_min=False (abertura) retorna 0
- _replicate_to_slave não envia comando se volume=0

**Pass Criteria**: ✅ Replicação cancelada, status SKIPPED, erro loggado

---

### 2.2 Volume Excede Máximo
**Cenário**: Volume > VOLUME_MAX → limitado ao máximo

**Setup**:
- Slave CLEAR: VOLUME_MAX = 100 lotes para EURUSD

**Steps**:
1. Master abre BUY EURUSD 200 lots (impossível em dados reais, mas testar lógica)
2. CLEAR deveria receber comando com volume = 100 (capped)
3. Verificar Historico: slave_lot = 100
4. Verificar Logs: "⚠️ Volume XXX excede máximo. Limitado a 100"

**Pass Criteria**: ✅ Volume capped ao máximo, operação executada com volume correto

---

### 2.3 Volume com STEP (Forex fracionário)
**Cenário**: Volume arredondado corretamente ao VOLUME_STEP

**Setup**:
- EURUSD: VOLUME_STEP = 0.01
- Master abre 1.234 lots (teste se arredonda)

**Steps**:
1. Master abre BUY EURUSD 1.234 lots
2. Verificar ea logs: normalize_volume arredonda para 1.23 (floor)
3. Verificar Historico: slave_lot = 1.23 (ou 1.20 se floor agressivo)
4. Verificar Logs: cálculo exato do arredondamento

**Pass Criteria**: ✅ Volume arredondado corretamente ao STEP

---

## 3. TESTES DE DETECÇÃO DE OPERAÇÕES ALIENÍGENAS

### 3.1 Manual Trade no Slave
**Cenário**: Operador abre trade manual no Slave (sem magic number) → popup detecta

**Setup**:
- Todos os EAs conectados e rodando
- Magic number configurado = 123456789

**Steps**:
1. Abrir MT5 do slave CLEAR
2. Abrir MANUALMENTE (sem EA): BUY EURUSD 0.5 lot
3. Observar deal no histórico: magic=0 (ou diferente)
4. Aguardar OnTradeTransaction do EA do slave
5. Verificar popup na UI: "Trade NAO originado pelo CopyTrade detectado!"
   - Corretora: CLEAR
   - Operacao: BUY EURUSD 0.5
   - Magic esperado: 123456789, encontrado: 0
6. Verificar Logs: "ALIEN TRADE em CLEAR: BUY EURUSD 0.5 lotes (magic=0, esperado=123456789)"

**Validação Técnica**:
- DEAL_MAGIC != g_magic_number dispara ALIEN_TRADE event
- ZmqMessageHandler processa ALIEN_TRADE e emite signal
- MainWindow._on_alien_trade_detected mostra popup

**Pass Criteria**: ✅ Popup aparece corretamente, dados precisos

---

### 3.2 Trade de Outro MT5 (mesmo account)
**Cenário**: Outro terminal MT5 abre trade na mesma conta de slave → detecta como alien

**Setup**:
- Slave CLEAR conectado ao CopyTrade (EA rodar lá)
- Outro terminal MT5 também conectado à mesma CLEAR conta

**Steps**:
1. No outro terminal: abrir BUY WIN20 1.0 lot manualmente
2. No EA do primeiro terminal: OnTradeTransaction dispara
3. Verificar magic do deal: será 0 ou magic de outro EA
4. Se magic != 123456789 → ALIEN_TRADE popup

**Pass Criteria**: ✅ Detecta trade de outro terminal

---

### 3.3 Verifying Magic Number Persistence
**Cenário**: Magic number salvo e carregado corretamente entre restarts

**Setup**: Magic number = 999999999 na GUI

**Steps**:
1. Abrir Settings → CopyTrade → Magic Number = 999999999
2. Salvar
3. Fechar EPCopyFlow completamente
4. Reabrir EPCopyFlow
5. Conectar EAs
6. Verificar Logs: "Magic number configurado em [broker]: 999999999"
7. Fazer trade manual no slave → deve detectar magic != 999999999

**Pass Criteria**: ✅ Magic number persistido e aplicado corretamente

---

## 4. TESTES DE RESILIÊNCIA

### 4.1 Reconexão após Desconexão (Network Outage)
**Cenário**: Simular desconexão e reconexão — trades não se perdem, rastreamento continua

**Setup**:
- Master + 2 Slaves rodando normalmente
- Posição aberta em master

**Steps**:
1. Master abre BUY EURUSD 1.0, slaves replicam
2. Simular network outage: desconectar cabo Ethernet / bloquear no firewall
3. Aguardar 10-15s (heartbeat vai falhar)
4. Verificar UI: Brokers ficam "Desconectado" (status cinza)
5. Reconectar rede
6. Aguardar heartbeat: EA re-registra (REGISTER event)
7. Verificar UI: Brokers voltam para "Conectado"
8. Master fecha BUY → slaves devem fechar também (verificar DB mapping slave_ticket)
9. Verificar Historico: close operations refletem os tickets corretos

**Validação Técnica**:
- position_map em memória + open_positions no DB = redundância
- Ao reconectar, DB deve ter informação ticket para cada slave
- Fechamento funciona com os tickets persistidos

**Pass Criteria**: ✅ Reconexão funciona, positions são mantidas, closing funciona

---

### 4.2 Restart do Python (EPCopyFlow)
**Cenário**: Fechar e reabrir EPCopyFlow — DB carrega estado, continua normal

**Setup**:
- Master com 2 slaves conectados
- Posição aberta (ex: BUY EURUSD em 3 contas)

**Steps**:
1. Verificar DB: open_positions tem 2 rows (position_id + 2 slaves)
2. Fechar EPCopyFlow
3. Verificar master/slaves: posições CONTINUAM abertas no MT5 (EA continua rodando)
4. Reabrir EPCopyFlow
5. Conectar: EAs registram-se novamente
6. Dashboard carrega e mostra posições (carregadas do DB)
7. Master fecha posição
8. Verificar Python consegue enviar CLOSE com slave_ticket correto (carregado do DB)
9. Confirmação de fechamento em todos os slaves

**Validação Técnica**:
- position_map é carregado do DB em inicialização (check se há código disso)
- Ou, ao reconectar, Python busca open_positions e reconstrói mapping

**Pass Criteria**: ✅ Restart transparente, posições recuperadas, closing funciona

---

### 4.3 Restart do MT5 (Slave)
**Cenário**: MT5 de slave reinicia — position re-sincroniza

**Setup**:
- Master com slave rodando, posição BUY aberta

**Steps**:
1. Fechar Terminal MT5 do slave (kill processo)
2. Aguardar: EA não consegue enviar heartbeat
3. UI mostra slave desconectado (cinza)
4. Process monitor do slave re-inicia MT5 (se configurado)
5. Ou, usuário re-abre MT5 manualmente
6. EA reconecta, envia REGISTER
7. Master fecha posição
8. Python envia CLOSE com slave_ticket ao slave
9. Verificar: slave recebe e executa fechamento

**Validação Técnica**:
- Mesmo com MT5 down, position_id e slave_ticket estão no DB
- Ao reconectar, Python pode re-enviar CLOSE se ainda estiver pending

**Pass Criteria**: ✅ Sincronização recuperada, closing funciona

---

### 4.4 EA do Slave Parado (MT5 rodando, EA off)
**Cenário**: MT5 slave rodando mas EA desabilitado — tenta replicar, recebe timeout

**Setup**:
- Master rodando com EA
- Slave MT5 aberto, but EA removido/desabilitado

**Steps**:
1. Master abre BUY
2. Python tenta enviar comando ao slave
3. Aguardar timeout (ZMQ timeout = 5s default)
4. Verificar Historico: status=FAILED, erro="timeout" ou "não conectado"
5. Verificar Logs: aviso "⚠️ Falha ao replicar para [slave]: timeout"

**Pass Criteria**: ✅ Timeout detectado, erro loggado, não trava

---

## 5. TESTES DE SELL-THROUGH PREVENTION

### 5.1 Attempt Close mais do que Slave tem
**Cenário**: Network lag causa desconexão entre close no master e update no slave DB → tenta fechar 2.0 do que tem 1.0

**Setup**:
- Slave com posição 1.0 BUY aberta
- Simular lag: close command envia volume 2.0 (erro)

**Steps**:
1. Master fecha 2.0 (ou partial reduz a 2.0 de forma inválida)
2. Python calcula volume para slave (deveria ser 1.0)
3. normalize_and_cap limita ao slave_volume_current (1.0)
4. Comando enviado com volume=1.0 (capped)
5. Verificar Historico: operação executa com volume correto
6. Verificar Logs: "⚠️ SELL-THROUGH CAP: 2.0 > slave_vol=1.0. Limitando a 1.0"

**Pass Criteria**: ✅ Cap previne overdose, volume respeitado

---

## 6. TESTES DE UI

### 6.1 Dashboard Refresh em Real Time
**Cenário**: Abrir trade no master → dashboard atualiza em <1s

**Steps**:
1. Abrir Dashboard
2. Master abre BUY EURUSD
3. Aguardar <1s
4. Verificar: "Total Copias" incrementa, card do master mostra posição
5. Todos os slave cards mostram posição aberta
6. Verificar lucro/prejuízo updated em tempo real (a cada quote)

**Pass Criteria**: ✅ UI responsiva, dados sincronizados

---

### 6.2 Settings Page - Magic Number Save
**Cenário**: Alterar magic number, salvar, verificar persistence

**Steps**:
1. Abrir Configurações → CopyTrade
2. Alterar Magic Number para 888888888
3. Alterar Heartbeat para 10 segundos
4. Clicar "Salvar"
5. Verificar popup: "Configuracoes salvas. Alteracoes de CopyTrade serao aplicadas na proxima conexao dos EAs."
6. Fechar e reabrir EPCopyFlow
7. Configurações → CopyTrade: valores devem estar salvos (888888888, 10)
8. Verificar config.ini: [CopyTrade] magic_number=888888888, heartbeat_interval=10

**Pass Criteria**: ✅ Settings persistem, valores corretos

---

### 6.3 Alien Trade Popup
**Cenário**: Já testado em 3.1 — validar formatação e informações

**Steps**: Ver seção 3.1

**Pass Criteria**: ✅ Popup mostra corretamente

---

## 7. TESTES DE EDGE CASES

### 7.1 Multiple Positions Same Symbol (Futures, não Forex)
**Cenário**: Em Futures com HEDGE, múltiplas posições — verificar que em NETTING não há esse caso

**Steps**:
1. Tentar abrir 2 POSIÇÕESindependentes no mesmo símbolo em NETTING
   - NETTING só permite 1 posição por símbolo
2. Verificar: EA rejeita ou fecha a primeira?

**Pass Criteria**: ✅ Comportamento definido e loggado

---

### 7.2 Símbolo Desativado no Slave
**Cenário**: Master trata símbolo X, mas slave não tem X (não tradeable)

**Steps**:
1. Master abre BUY OILUSD (disponível)
2. Slave CLEAR: OILUSD não disponível (horário de mercado, símbolo off, etc)
3. Python tenta replicar
4. EA do slave: GET_SYMBOL_INFO falha ou retorna specs inválidas
5. Verificar Historico: status=FAILED, erro="símbolo não disponível" ou similar

**Pass Criteria**: ✅ Erro tratado graciosamente

---

### 7.3 Spread muito Grande (slippage detection)
**Cenário**: Mercado com spread grande — trade executa com slippage

**Setup**:
- max_price_deviation = 30 pips (configurável)

**Steps**:
1. Master abre em bid=1.0850, ask=1.0855
2. Slave tenta abrir com comando price=1.0850
3. Market move para bid=1.0920, ask=1.0925 (70 pips!)
4. EA trata com TRADE_REQUEST_DEVIATION=10 (default)
5. Trade executa com slippage
6. Verificar: trade_event mostra result_price ≠ request_price
7. Verificar Historico: "alert" ou "warning" sobre slippage?

**Pass Criteria**: ✅ Trade executa, slippage loggado ou alertado

---

## 8. TESTES DE PERFORMANCE

### 8.1 Latência ZMQ (Command → Response)
**Cenário**: Medir latência de uma operação de trade

**Steps**:
1. Abrir Logs
2. Master abre trade
3. Verificar timestamps:
   - "MASTER [...]" log timestamp
   - "COPY [slave_key]" log timestamp
4. Diferença = latência Python + network
5. Expected: <500ms para local network

**Pass Criteria**: ✅ Latência aceitável (<1s)

---

### 8.2 Memory Leak (long-running test)
**Cenário**: Deixar rodando por 1-2 horas com trades frequentes

**Setup**:
- Master com bot que abre/fecha trades a cada 30s
- Monitorar memory usage do Python (Task Manager)

**Steps**:
1. Iniciar EPCopyFlow
2. Rodar bot por 60+ minutos
3. A cada 10 minutos, verificar:
   - Memory usage (deve estabilizar, não crescer)
   - DB copytrade_history (pode crescer, expected)
   - position_map em memória (deve limpar closed positions)
4. Verificar Logs: sem erros cumulativos

**Pass Criteria**: ✅ Memory estável, sem leaks aparentes

---

## 9. TESTES DE SEGURANÇA / VALIDATION

### 9.1 SQL Injection Attempt (broker_key em query)
**Cenário**: Verificar que broker_key é safely parameterized

**Steps**:
1. Tentar registrar broker com nome = `"; DROP TABLE copytrade_history; --`
2. Sistema deve rejeitar caracteres inválidos OU safely escape
3. Verificar DB: tabelas intactas
4. Verificar Logs: erro de validação

**Pass Criteria**: ✅ Injeção prevenida, dados intactos

---

### 9.2 Path Traversal (config path)
**Cenário**: Verificar que caminhos são validados

**Steps**: N/A se não há file upload, mas confirmar paths são absolutos e validados

**Pass Criteria**: ✅ Safe

---

## 10. TESTE INTEGRADO (End-to-End)

### Cenário Completo: Day Trading Simulation
**Duração**: 30 minutos de mercado

**Setup**:
- Master: bot que abre/fecha 5-10 trades (BUY/SELL, various lots)
- Slaves: 2-3 conectados com multipliers diferentes
- Magic number detectando aliens

**Expected Flow**:
1. Cada trade do master é replicado a todos os slaves
2. Volumes calculados corretamente (multipliers aplicados)
3. Fechamentos parciais e totais funcionam
4. DB em sync (open_positions, copytrade_history)
5. Dashboard atualiza em tempo real
6. Sem erros críticos

**Metrics to Verify**:
- ✅ Total trades = master trades × 2-3 slaves
- ✅ Success rate > 99%
- ✅ Volume correlation (slave_lot = master_lot × multiplier, within floating point tolerance)
- ✅ Zero orphaned positions (sem posições abertas sem rastreamento)
- ✅ Zero sell-through violations
- ✅ DB consistent (sum of slave_lot matches master_lot)
- ✅ Performance: latency <1s, no freezes

---

## Checklist de Execução

- [ ] 1.1 Abertura Simples
- [ ] 1.2 Fechamento Total
- [ ] 1.3 Fechamento Parcial
- [ ] 1.4 ADD
- [ ] 1.5 REDUCE
- [ ] 1.6 Position Flipping
- [ ] 2.1 Volume Inválido
- [ ] 2.2 Volume Máximo
- [ ] 2.3 Volume STEP
- [ ] 3.1 Manual Trade no Slave
- [ ] 3.2 Trade de Outro MT5
- [ ] 3.3 Magic Number Persistence
- [ ] 4.1 Reconexão Network
- [ ] 4.2 Restart Python
- [ ] 4.3 Restart MT5 Slave
- [ ] 4.4 EA Parado
- [ ] 5.1 Sell-Through Prevention
- [ ] 6.1 Dashboard Refresh
- [ ] 6.2 Settings Save
- [ ] 6.3 Alien Popup
- [ ] 7.1 Multiple Positions
- [ ] 7.2 Símbolo Desativado
- [ ] 7.3 Spread Grande
- [ ] 8.1 Latência ZMQ
- [ ] 8.2 Memory Leak
- [ ] 9.1 SQL Injection
- [ ] 9.2 Path Traversal
- [ ] 10 Teste Integrado

---

## Ferramentas Recomendadas

- **Logs**: Check `main.py` logs em tempo real (Logs page da UI)
- **DB Inspection**: SQLite viewer (DBeaver, SQLiteStudio) para verificar copytrade_history, open_positions
- **MT5 Profiler**: Task Manager para monitorar memory/CPU
- **Network Simulation**: Disconnect network cable ou block ports no firewall para reconexão tests
- **Trading Bot**: Script Python simples que abre/fecha trades no master para teste integrado

---

## Notas

1. **Ordem Recomendada**: Fazer 1.x → 2.x → 3.x → 4.x → 5.x → 6.x → 7.x → 8.x → 9.x → 10
2. **Critical Path**: 1.1, 1.2, 3.1, 4.1, 5.1 são prioritários
3. **Documentação**: Para cada teste, tomar screenshot de:
   - Dashboard após operação
   - Historico com resultado
   - Logs mostrando operações
   - DB final state (via SQLite viewer)
4. **Problemas Encontrados**: Abrir issue no GitHub com screenshot + logs + DB dump
