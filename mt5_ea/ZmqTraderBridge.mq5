//+------------------------------------------------------------------+
//| EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000           |
//| ZmqTraderBridge.mq5                                              |
//| MQL5 <-> Python ZeroMQ Bridge para CopyTrade                     |
//| 2 sockets: CommandSocket (DEALER) + EventSocket (PUB)            |
//| Modo MASTER: detecta trades e publica eventos                    |
//| Modo SLAVE: executa trades recebidos do Python                   |
//+------------------------------------------------------------------+
#property copyright "EPFilho"
#property link      "epfilho73@gmail.com"
#property version   "2.00"
#property strict

#include <Zmq/Zmq.mqh>
#include <Json.mqh>
#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Bloco 1 - Configuração e Conexão ZMQ                            |
//+------------------------------------------------------------------+

//--- Parâmetros configuráveis
input int    InpTimerIntervalMs  = 200;   // Intervalo do timer (ms)
input bool   InpDebugLog         = true;  // Ativar logs

//--- Variáveis globais
Context context;
Socket  command_socket(context, ZMQ_DEALER);  // Bidirecional: admin + trade
Socket  event_socket(context, ZMQ_PUB);       // Unidirecional: EA → Python
bool    g_is_connected = false;
CTrade  trade;

//--- Config lidas do config.ini
string g_brokerKey = "";
string g_role = "SLAVE";  // MASTER ou SLAVE
int    g_commandPort = 0;
int    g_eventPort = 0;

//--- Monitoramento de trade_allowed
bool g_last_trade_allowed = false;
bool g_initial_trade_allowed_sent = false;

//--- Monitoramento de conexão com o servidor da corretora
bool g_last_terminal_connected = false;
bool g_initial_connection_status_sent = false;

//--- Heartbeat periódico (enviado pelo EA para o Python)
ulong g_heartbeat_interval_ms = 5000;  // Padrão: 5 segundos (será configurado pelo Python)
ulong g_last_heartbeat_time = 0;       // Timestamp do último heartbeat enviado

//--- Magic number para identificar trades do CopyTrade (configurado pelo Python)
long g_magic_number = 0;               // 0 = não configurado (desabilita detecção de aliens)

//+------------------------------------------------------------------+
//| Função auxiliar para trim de string                              |
//+------------------------------------------------------------------+
string TrimString(string s)
{
   StringTrimLeft(s);
   StringTrimRight(s);
   return s;
}

//+------------------------------------------------------------------+
//| Lê as configurações do arquivo config.ini                        |
//+------------------------------------------------------------------+
bool ReadConfigFile(string &brokerKey, string &role, int &commandPort, int &eventPort)
{
   int file_handle = FileOpen("config.ini", FILE_READ|FILE_ANSI|FILE_TXT);
   if(file_handle == INVALID_HANDLE)
   {
      int error_code = GetLastError();
      Print("Erro ao abrir config.ini. Erro code = ", IntegerToString(error_code));
      string file_path = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\config.ini";
      Print("Caminho esperado: ", file_path);
      return false;
   }

   string currentSection = "";
   while(!FileIsEnding(file_handle))
   {
      string linha = FileReadString(file_handle);
      linha = TrimString(linha);

      // Detecta seções
      if(StringFind(linha, "[") == 0)
      {
         currentSection = linha;
         continue;
      }

      int posicaoIgual = StringFind(linha, "=");
      if(posicaoIgual <= 0) continue;

      string chave = TrimString(StringSubstr(linha, 0, posicaoIgual));
      string valor = TrimString(StringSubstr(linha, posicaoIgual + 1));

      if(currentSection == "[General]")
      {
         if(chave == "BrokerKey") brokerKey = valor;
         else if(chave == "Role") role = valor;
      }
      else if(currentSection == "[Ports]")
      {
         if(chave == "CommandPort") commandPort = (int)StringToInteger(valor);
         else if(chave == "EventPort") eventPort = (int)StringToInteger(valor);
      }
   }
   FileClose(file_handle);

   if(InpDebugLog)
   {
      PrintFormat("Config: BrokerKey=%s, Role=%s, CommandPort=%d, EventPort=%d",
                  brokerKey, role, commandPort, eventPort);
   }
   return true;
}

//+------------------------------------------------------------------+
//| Valida portas                                                    |
//+------------------------------------------------------------------+
bool ValidatePorts()
{
   if(g_commandPort == g_eventPort || g_commandPort <= 0 || g_eventPort <= 0)
   {
      Print("Erro: Portas inválidas ou duplicadas (CommandPort=", g_commandPort,
            ", EventPort=", g_eventPort, ")");
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Serializa JSON de forma robusta                                 |
//+------------------------------------------------------------------+
void RobustJsonSerialize(JSONNode &json_message, string &out)
{
   // CRÍTICO: string NUNCA pode ser retornada via return — MQL5 trunca em ~255 chars.
   // Toda a cadeia usa passagem por referência (SerializeTo → out → SendJsonMessage).
   out = "";
   json_message.SerializeTo(out);

   if(StringLen(out) == 0)
   {
      // Fallback: Serialize() padrão (para Json.mqh sem SerializeTo)
      out = json_message.Serialize();
      if(StringLen(out) == 0)
      {
         Print("WARN: JSON serializado vazio");
         out = "{}";
      }
   }
}

//+------------------------------------------------------------------+
//| Enviar mensagem JSON por socket específico                      |
//+------------------------------------------------------------------+
bool SendJsonMessage(JSONNode &json_message, Socket &target_socket, string socket_name="Command")
{
   json_message["broker_key"] = g_brokerKey;
   if(!g_is_connected)
   {
      Print("ERROR: Tentativa de envio sem conexão em ", socket_name);
      return false;
   }
   string message_str;
   RobustJsonSerialize(json_message, message_str);
   if(InpDebugLog)
      Print("TX (", socket_name, "): ", message_str);

   ZmqMsg msg(message_str);
   bool sent = target_socket.send(msg);
   if(!sent)
   {
      PrintFormat("ERROR: Falha ao enviar em %s. GetLastError(): %d", socket_name, GetLastError());
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Mensagens de Sistema                                            |
//+------------------------------------------------------------------+
bool SendRegisterMessage()
{
   JSONNode message;
   message["type"] = "SYSTEM";
   message["event"] = "REGISTER";
   message["role"] = g_role;
   message["mt5_build"] = (long)TerminalInfoInteger(TERMINAL_BUILD);
   message["timestamp_mql"] = (long)TimeCurrent();
   PrintFormat("Enviando REGISTER para %s (Role=%s)", g_brokerKey, g_role);
   return SendJsonMessage(message, command_socket, "Command");
}

bool SendUnregisterMessage()
{
   if(!g_is_connected) return false;
   JSONNode message;
   message["type"] = "SYSTEM";
   message["event"] = "UNREGISTER";
   message["timestamp_mql"] = (long)TimeCurrent();
   PrintFormat("Enviando UNREGISTER para %s", g_brokerKey);
   return SendJsonMessage(message, command_socket, "Command");
}

//+------------------------------------------------------------------+
//| Resposta de erro padrão                                         |
//+------------------------------------------------------------------+
bool SendErrorResponse(const string request_id, const string error_message)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "ERROR";
   response["error_message"] = error_message;
   return SendJsonMessage(response, command_socket, "Command");
}

//+------------------------------------------------------------------+
//| Bloco 2 - Comandos Administrativos                              |
//| Respondidos por MASTER e SLAVE igualmente.                       |
//+------------------------------------------------------------------+

void HandlePingCommand(const string request_id, JSONNode *payload_node_ptr)
{
   if(InpDebugLog) Print("Recebido PING.");
   long original_timestamp = 0;
   if(CheckPointer(payload_node_ptr) != POINTER_INVALID)
   {
      JSONNode *ts_node_ptr = (*payload_node_ptr)["timestamp"];
      if(CheckPointer(ts_node_ptr) != POINTER_INVALID)
         original_timestamp = ts_node_ptr.ToInteger();
   }
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["original_timestamp"] = original_timestamp;
   response["pong_timestamp_mql"] = (long)TimeCurrent();
   SendJsonMessage(response, command_socket, "Command");
}

void HandleGetStatusInfoCommand(const string request_id, JSONNode *payload_node_ptr)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["trade_allowed"] = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   response["balance"] = AccountInfoDouble(ACCOUNT_BALANCE);
   response["pong_timestamp_mql"] = (long)TimeCurrent();
   SendJsonMessage(response, command_socket, "Command");
}

void HandleGetAccountBalanceCommand(const string request_id)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["balance"] = AccountInfoDouble(ACCOUNT_BALANCE);
   response["equity"] = AccountInfoDouble(ACCOUNT_EQUITY);
   response["currency"] = AccountInfoString(ACCOUNT_CURRENCY);
   SendJsonMessage(response, command_socket, "Command");
}

void HandleGetAccountFlagsCommand(const string request_id)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["trade_allowed"] = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   response["expert_enabled"] = (bool)AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
   SendJsonMessage(response, command_socket, "Command");
}

void HandleGetAccountMarginCommand(const string request_id)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["margin"] = AccountInfoDouble(ACCOUNT_MARGIN);
   response["free_margin"] = AccountInfoDouble(ACCOUNT_FREEMARGIN);
   response["margin_level"] = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   SendJsonMessage(response, command_socket, "Command");
}

void HandleGetAccountModeCommand(const string request_id)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";

   // Verificar modo da conta
   int margin_mode = (int)AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   string mode_str;

   if(margin_mode == ACCOUNT_MARGIN_MODE_RETAIL_NETTING)
      mode_str = "Netting";
   else if(margin_mode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
      mode_str = "Hedging";
   else if(margin_mode == ACCOUNT_MARGIN_MODE_EXCHANGE)
      mode_str = "Exchange";
   else
      mode_str = "Unknown";

   response["account_mode"] = mode_str;
   response["margin_mode_code"] = (long)margin_mode;

   SendJsonMessage(response, command_socket, "Command");
}

void HandleGetSymbolInfoCommand(const string request_id, JSONNode &payload)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;

   // Extrair símbolo do payload
   JSONNode *symbol_node = payload["symbol"];
   if(CheckPointer(symbol_node) == POINTER_INVALID)
   {
      response["status"] = "ERROR";
      response["error_message"] = "symbol parameter required";
      SendJsonMessage(response, command_socket, "Command");
      return;
   }

   string symbol = symbol_node.ToString();

   // Verificar se símbolo existe
   if(!SymbolSelect(symbol, true))
   {
      response["status"] = "ERROR";
      response["error_message"] = StringFormat("Symbol not found: %s", symbol);
      SendJsonMessage(response, command_socket, "Command");
      return;
   }

   response["status"] = "OK";
   response["symbol"] = symbol;
   response["volume_min"] = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   response["volume_max"] = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   response["volume_step"] = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   response["digits"] = (long)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   response["trade_mode"] = (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);

   SendJsonMessage(response, command_socket, "Command");
}

void HandleSetHeartbeatIntervalCommand(const string request_id, JSONNode &payload)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;

   // Extrair intervalo do payload
   JSONNode *interval_node = payload["heartbeat_interval_ms"];
   if(CheckPointer(interval_node) == POINTER_INVALID)
   {
      response["status"] = "ERROR";
      response["error_message"] = "heartbeat_interval_ms não fornecido";
      SendJsonMessage(response, command_socket, "Command");
      return;
   }

   long interval = StringToInteger(interval_node.ToString());
   if(interval < 1000 || interval > 600000)  // 1s a 10 minutos
   {
      response["status"] = "ERROR";
      response["error_message"] = StringFormat("Intervalo inválido: %d (deve ser 1000-600000 ms)", interval);
      SendJsonMessage(response, command_socket, "Command");
      return;
   }

   g_heartbeat_interval_ms = (ulong)interval;
   g_last_heartbeat_time = GetTickCount64();  // Reset timing

   response["status"] = "OK";
   response["heartbeat_interval_ms"] = interval;
   SendJsonMessage(response, command_socket, "Command");

   PrintFormat("Intervalo de heartbeat configurado: %d ms", interval);
}

void HandleSetMagicNumberCommand(const string request_id, JSONNode &payload)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;

   JSONNode *magic_node = payload["magic_number"];
   if(CheckPointer(magic_node) == POINTER_INVALID)
   {
      response["status"] = "ERROR";
      response["error_message"] = "magic_number nao fornecido";
      SendJsonMessage(response, command_socket, "Command");
      return;
   }

   long magic = StringToInteger(magic_node.ToString());
   if(magic <= 0)
   {
      response["status"] = "ERROR";
      response["error_message"] = StringFormat("magic_number invalido: %lld", magic);
      SendJsonMessage(response, command_socket, "Command");
      return;
   }

   g_magic_number = magic;
   trade.SetExpertMagicNumber((ulong)magic);

   response["status"] = "OK";
   response["magic_number"] = magic;
   SendJsonMessage(response, command_socket, "Command");

   PrintFormat("Magic number configurado: %lld (CTrade atualizado)", magic);
}

void HandleGetPositionsCommand(const string request_id)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";

   // FLATTENAR para evitar bug de serialização de objetos aninhados em array
   int total = PositionsTotal();
   response["positions_count"] = (long)total;

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket))
      {
         string prefix = StringFormat("pos_%d_", i);
         response[prefix + "ticket"] = (long)ticket;
         response[prefix + "symbol"] = PositionGetString(POSITION_SYMBOL);
         response[prefix + "type"] = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL";
         response[prefix + "volume"] = PositionGetDouble(POSITION_VOLUME);
         response[prefix + "price_open"] = PositionGetDouble(POSITION_PRICE_OPEN);
         response[prefix + "sl"] = PositionGetDouble(POSITION_SL);
         response[prefix + "tp"] = PositionGetDouble(POSITION_TP);
         response[prefix + "profit"] = PositionGetDouble(POSITION_PROFIT);
         response[prefix + "comment"] = PositionGetString(POSITION_COMMENT);
      }
   }
   SendJsonMessage(response, command_socket, "Command");
}

void HandleGetOrdersCommand(const string request_id)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";

   JSONNode orders_array;
   for(int i = 0; i < OrdersTotal(); i++)
   {
      ulong ticket = OrderGetTicket(i);
      if(OrderSelect(ticket))
      {
         JSONNode ord;
         ord["ticket"] = (long)ticket;
         ord["symbol"] = OrderGetString(ORDER_SYMBOL);
         ord["type"] = EnumToString((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE));
         ord["volume"] = OrderGetDouble(ORDER_VOLUME_CURRENT);
         ord["price"] = OrderGetDouble(ORDER_PRICE_OPEN);
         ord["sl"] = OrderGetDouble(ORDER_SL);
         ord["tp"] = OrderGetDouble(ORDER_TP);
         orders_array.Add(ord);
      }
   }
   response["orders"] = orders_array;
   SendJsonMessage(response, command_socket, "Command");
}

void SendHeartbeat()
{
   // Envia heartbeat periódico com as posições atuais
   JSONNode heartbeat;
   heartbeat["type"] = "STREAM";
   heartbeat["event"] = "HEARTBEAT";
   heartbeat["timestamp_mql"] = (long)TimeCurrent();
   heartbeat["role"] = g_role;

   // Flattenizar posições (mesmo formato que GET_POSITIONS)
   int total = PositionsTotal();
   heartbeat["positions_count"] = (long)total;

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket))
      {
         string prefix = StringFormat("pos_%d_", i);
         heartbeat[prefix + "ticket"] = (long)ticket;
         heartbeat[prefix + "symbol"] = PositionGetString(POSITION_SYMBOL);
         heartbeat[prefix + "type"] = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL";
         heartbeat[prefix + "volume"] = PositionGetDouble(POSITION_VOLUME);
         heartbeat[prefix + "price_open"] = PositionGetDouble(POSITION_PRICE_OPEN);
         heartbeat[prefix + "profit"] = PositionGetDouble(POSITION_PROFIT);
      }
   }

   SendJsonMessage(heartbeat, event_socket, "Event");
}

void HandleGetHistoryTradesCommand(const string request_id, JSONNode &payload)
{
   long start_time = payload["start_time"].ToInteger();
   long end_time = payload["end_time"].ToInteger();

   if(start_time <= 0 || end_time <= 0 || start_time >= end_time)
   {
      end_time = TimeCurrent();
      start_time = end_time - 7 * 24 * 60 * 60;
   }

   if(!HistorySelect((datetime)start_time, (datetime)end_time))
   {
      SendErrorResponse(request_id, "Falha ao selecionar histórico");
      return;
   }

   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";

   struct PositionData {
      ulong position_id;
      string symbol;
      string type;
      double volume;
      double price_open;
      double price_close;
      double profit;
      long time_open;
      long time_close;
      string comment;
      bool has_entry_in;
      bool has_entry_out;
   };

   PositionData positions[];
   int position_count = 0;

   int total_deals = HistoryDealsTotal();
   CDealInfo dealInfo;

   for(int i = 0; i < total_deals; i++)
   {
      if(dealInfo.SelectByIndex(i))
      {
         ENUM_DEAL_TYPE deal_type = dealInfo.DealType();
         if(deal_type == DEAL_TYPE_BUY || deal_type == DEAL_TYPE_SELL)
         {
            ulong position_id = dealInfo.PositionId();
            ENUM_DEAL_ENTRY entry = dealInfo.Entry();

            int pos_index = -1;
            for(int j = 0; j < position_count; j++)
            {
               if(positions[j].position_id == position_id)
               {
                  pos_index = j;
                  break;
               }
            }

            if(pos_index == -1)
            {
               ArrayResize(positions, position_count + 1);
               pos_index = position_count;
               positions[pos_index].position_id = position_id;
               positions[pos_index].symbol = dealInfo.Symbol();
               positions[pos_index].type = deal_type == DEAL_TYPE_BUY ? "BUY" : "SELL";
               positions[pos_index].volume = dealInfo.Volume();
               positions[pos_index].comment = dealInfo.Comment();
               positions[pos_index].has_entry_in = false;
               positions[pos_index].has_entry_out = false;
               positions[pos_index].profit = 0;
               position_count++;
            }

            if(entry == DEAL_ENTRY_IN)
            {
               positions[pos_index].price_open = dealInfo.Price();
               positions[pos_index].time_open = (long)dealInfo.Time();
               positions[pos_index].has_entry_in = true;
            }
            else if(entry == DEAL_ENTRY_OUT)
            {
               positions[pos_index].price_close = dealInfo.Price();
               positions[pos_index].time_close = (long)dealInfo.Time();
               positions[pos_index].profit += dealInfo.Profit();
               positions[pos_index].has_entry_out = true;
            }
         }
      }
   }

   JSONNode positions_array;
   for(int i = 0; i < position_count; i++)
   {
      if(positions[i].has_entry_in && positions[i].has_entry_out)
      {
         JSONNode position;
         position["ticket"] = (long)positions[i].position_id;
         position["symbol"] = positions[i].symbol;
         position["type"] = positions[i].type;
         position["volume"] = positions[i].volume;
         position["price_open"] = positions[i].price_open;
         position["price_close"] = positions[i].price_close;
         position["profit"] = positions[i].profit;
         position["time_open"] = positions[i].time_open;
         position["time_close"] = positions[i].time_close;
         position["comment"] = positions[i].comment;
         positions_array.Add(position);
      }
   }

   response["positions"] = positions_array;
   SendJsonMessage(response, command_socket, "Command");
}

//+------------------------------------------------------------------+
//| Bloco 3 - Comandos de Trading                                   |
//| Executados apenas pelo SLAVE. MASTER rejeita comandos de trade.  |
//+------------------------------------------------------------------+

void HandleTradeBuyCommand(const string request_id, JSONNode &payload)
{
   if(g_role == "MASTER")
   {
      SendErrorResponse(request_id, "MASTER não aceita comandos de trade");
      return;
   }

   string symbol = payload["symbol"].ToString();
   double volume = payload["volume"].ToDouble();
   double price = payload["price"].ToDouble();
   double sl = payload["sl"].ToDouble();
   double tp = payload["tp"].ToDouble();
   int deviation = (int)payload["deviation"].ToInteger();
   string comment = payload["comment"].ToString();

   trade.SetDeviationInPoints(deviation);
   if(!trade.Buy(volume, symbol, price, sl, tp, comment))
   {
      SendErrorResponse(request_id, StringFormat("Falha BUY: %s", trade.ResultComment()));
      return;
   }

   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["retcode"] = (long)trade.ResultRetcode();
   response["result"] = trade.ResultComment();
   response["deal"] = (long)trade.ResultDeal();
   response["order"] = (long)trade.ResultOrder();
   response["volume"] = trade.ResultVolume();
   response["price"] = trade.ResultPrice();
   response["ticket"] = (long)trade.ResultDeal();
   SendJsonMessage(response, command_socket, "Command");
}

void HandleTradeSellCommand(const string request_id, JSONNode &payload)
{
   if(g_role == "MASTER")
   {
      SendErrorResponse(request_id, "MASTER não aceita comandos de trade");
      return;
   }

   string symbol = payload["symbol"].ToString();
   double volume = payload["volume"].ToDouble();
   double price = payload["price"].ToDouble();
   double sl = payload["sl"].ToDouble();
   double tp = payload["tp"].ToDouble();
   int deviation = (int)payload["deviation"].ToInteger();
   string comment = payload["comment"].ToString();

   trade.SetDeviationInPoints(deviation);
   if(!trade.Sell(volume, symbol, price, sl, tp, comment))
   {
      SendErrorResponse(request_id, StringFormat("Falha SELL: %s", trade.ResultComment()));
      return;
   }

   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["retcode"] = (long)trade.ResultRetcode();
   response["result"] = trade.ResultComment();
   response["deal"] = (long)trade.ResultDeal();
   response["order"] = (long)trade.ResultOrder();
   response["volume"] = trade.ResultVolume();
   response["price"] = trade.ResultPrice();
   response["ticket"] = (long)trade.ResultDeal();
   SendJsonMessage(response, command_socket, "Command");
}

void HandleTradePositionModifyCommand(const string request_id, JSONNode &payload)
{
   if(g_role == "MASTER")
   {
      SendErrorResponse(request_id, "MASTER não aceita comandos de trade");
      return;
   }

   long ticket = payload["ticket"].ToInteger();
   double sl = payload["sl"].ToDouble();
   double tp = payload["tp"].ToDouble();

   if(!trade.PositionModify(ticket, sl, tp))
   {
      SendErrorResponse(request_id, StringFormat("Falha modificar posição: %s", trade.ResultComment()));
      return;
   }

   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["retcode"] = (long)trade.ResultRetcode();
   response["result"] = trade.ResultComment();
   response["ticket"] = ticket;
   SendJsonMessage(response, command_socket, "Command");
}

void HandleTradePositionPartialCommand(const string request_id, JSONNode &payload)
{
   if(g_role == "MASTER")
   {
      SendErrorResponse(request_id, "MASTER não aceita comandos de trade");
      return;
   }

   long ticket = payload["ticket"].ToInteger();
   double volume = payload["volume"].ToDouble();

   if(!PositionSelectByTicket(ticket))
   {
      SendErrorResponse(request_id, "Posição não encontrada");
      return;
   }

   // PositionClosePartial pode retornar false mesmo com execução bem-sucedida (bug CTrade).
   // Verificar pelo retcode em vez do retorno do método.
   trade.PositionClosePartial(ticket, volume);
   uint retcode = trade.ResultRetcode();

   if(retcode != TRADE_RETCODE_DONE && retcode != TRADE_RETCODE_PLACED && retcode != TRADE_RETCODE_DONE_PARTIAL)
   {
      SendErrorResponse(request_id, StringFormat("Falha fechamento parcial (retcode=%d): %s", retcode, trade.ResultComment()));
      return;
   }

   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["retcode"] = (long)retcode;
   response["result"] = trade.ResultComment();
   response["deal"] = (long)trade.ResultDeal();
   response["order"] = (long)trade.ResultOrder();
   response["volume"] = trade.ResultVolume();
   response["ticket"] = ticket;
   SendJsonMessage(response, command_socket, "Command");
}

void HandleTradePositionCloseIdCommand(const string request_id, JSONNode &payload)
{
   // Emergency close bypassa proteção do MASTER
   bool is_emergency = false;
   JSONNode *emergency_node = payload["emergency"];
   if(CheckPointer(emergency_node) != POINTER_INVALID)
      is_emergency = (emergency_node.ToBool() || emergency_node.ToString() == "true");

   if(g_role == "MASTER" && !is_emergency)
   {
      SendErrorResponse(request_id, "MASTER não aceita comandos de trade");
      return;
   }

   long ticket = payload["ticket"].ToInteger();

   if(!trade.PositionClose(ticket))
   {
      SendErrorResponse(request_id, StringFormat("Falha fechar posição: %s", trade.ResultComment()));
      return;
   }

   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["retcode"] = (long)trade.ResultRetcode();
   response["result"] = trade.ResultComment();
   response["deal"] = (long)trade.ResultDeal();
   response["order"] = (long)trade.ResultOrder();
   response["ticket"] = ticket;
   SendJsonMessage(response, command_socket, "Command");
}

void HandleTradePositionCloseSymbolCommand(const string request_id, JSONNode &payload)
{
   if(g_role == "MASTER")
   {
      SendErrorResponse(request_id, "MASTER não aceita comandos de trade");
      return;
   }

   string symbol = payload["symbol"].ToString();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket) && PositionGetString(POSITION_SYMBOL) == symbol)
      {
         if(!trade.PositionClose(ticket))
         {
            SendErrorResponse(request_id, StringFormat("Falha fechar posição: %s", trade.ResultComment()));
            return;
         }
      }
   }

   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["retcode"] = TRADE_RETCODE_DONE;
   response["result"] = "Positions closed";
   SendJsonMessage(response, command_socket, "Command");
}

//+------------------------------------------------------------------+
//| Bloco 4 - Funções Principais do EA                              |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Inicialização do EA                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("EPCopyFlow EA: Inicializando...");

   if(!ReadConfigFile(g_brokerKey, g_role, g_commandPort, g_eventPort))
   {
      Alert("EPCopyFlow EA: Falha ao ler config.ini.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   if(StringLen(g_brokerKey) == 0 || StringFind(g_brokerKey, "-") <= 0)
   {
      Alert("EPCopyFlow EA: BrokerKey inválido!");
      return(INIT_PARAMETERS_INCORRECT);
   }

   // Validar Role
   if(g_role != "MASTER" && g_role != "SLAVE")
   {
      Alert("EPCopyFlow EA: Role inválido! Deve ser MASTER ou SLAVE. Recebido: ", g_role);
      return(INIT_PARAMETERS_INCORRECT);
   }

   if(!ValidatePorts())
      return(INIT_PARAMETERS_INCORRECT);

   // CommandSocket (DEALER - bidirecional)
   command_socket.setIdentity(g_brokerKey);
   if(!command_socket.bind(StringFormat("tcp://*:%d", g_commandPort)))
   {
      PrintFormat("Erro ao bind CommandSocket porta %d. GetLastError(): %d", g_commandPort, GetLastError());
      return(INIT_FAILED);
   }

   // EventSocket (PUB - EA → Python)
   if(!event_socket.bind(StringFormat("tcp://*:%d", g_eventPort)))
   {
      PrintFormat("Erro ao bind EventSocket porta %d. GetLastError(): %d", g_eventPort, GetLastError());
      return(INIT_FAILED);
   }

   g_is_connected = true;
   if(!SendRegisterMessage())
      Print("Falha ao enviar REGISTER.");

   if(!EventSetMillisecondTimer(InpTimerIntervalMs))
   {
      Print("Erro ao iniciar Timer! GetLastError():", GetLastError());
      g_is_connected = false;
      return(INIT_FAILED);
   }

   g_last_trade_allowed = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   g_last_terminal_connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   PrintFormat("EPCopyFlow EA: Inicializado. Role=%s, BrokerKey=%s", g_role, g_brokerKey);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Desinicialização do EA                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   PrintFormat("EPCopyFlow EA: Desinicializando... Razão: %d", reason);
   if(g_is_connected)
      SendUnregisterMessage();
   EventKillTimer();
   g_is_connected = false;

   command_socket.disconnect(StringFormat("tcp://*:%d", g_commandPort));
   event_socket.disconnect(StringFormat("tcp://*:%d", g_eventPort));

   Print("EPCopyFlow EA: Desinicialização completa.");
}

//+------------------------------------------------------------------+
//| OnTimer                                                          |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(!g_is_connected) return;

   // Processa comandos recebidos via CommandSocket
   CheckIncomingCommands();

   // Envio inicial de trade_allowed
   if(!g_initial_trade_allowed_sent)
   {
      bool current = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
      JSONNode msg;
      msg["type"] = "STREAM";
      msg["event"] = "TRADE_ALLOWED_UPDATE";
      msg["trade_allowed"] = current;
      msg["timestamp_mql"] = (long)TimeCurrent();
      SendJsonMessage(msg, event_socket, "Event");
      g_initial_trade_allowed_sent = true;
      g_last_trade_allowed = current;
   }

   // Detecta mudança de trade_allowed
   bool current_trade_allowed = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   if(current_trade_allowed != g_last_trade_allowed)
   {
      JSONNode msg;
      msg["type"] = "STREAM";
      msg["event"] = "TRADE_ALLOWED_UPDATE";
      msg["trade_allowed"] = current_trade_allowed;
      msg["timestamp_mql"] = (long)TimeCurrent();
      SendJsonMessage(msg, event_socket, "Event");
      g_last_trade_allowed = current_trade_allowed;
      if(InpDebugLog)
         PrintFormat("TRADE_ALLOWED_UPDATE: %s", current_trade_allowed ? "true" : "false");
   }

   // Envio inicial de connection_status
   if(!g_initial_connection_status_sent)
   {
      bool connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
      JSONNode msg;
      msg["type"] = "STREAM";
      msg["event"] = "CONNECTION_STATUS";
      msg["connected"] = connected;
      msg["timestamp_mql"] = (long)TimeCurrent();
      SendJsonMessage(msg, event_socket, "Event");
      g_initial_connection_status_sent = true;
      g_last_terminal_connected = connected;
   }

   // Detecta mudança de conexão com o servidor da corretora
   bool current_connected = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   if(current_connected != g_last_terminal_connected)
   {
      JSONNode msg;
      msg["type"] = "STREAM";
      msg["event"] = "CONNECTION_STATUS";
      msg["connected"] = current_connected;
      msg["timestamp_mql"] = (long)TimeCurrent();
      SendJsonMessage(msg, event_socket, "Event");
      g_last_terminal_connected = current_connected;
      if(InpDebugLog)
         PrintFormat("CONNECTION_STATUS: %s", current_connected ? "connected" : "disconnected");
   }

   // Envio periódico de heartbeat com posições
   ulong current_time = GetTickCount64();
   if(current_time - g_last_heartbeat_time >= g_heartbeat_interval_ms)
   {
      SendHeartbeat();
      g_last_heartbeat_time = current_time;
   }
}

//+------------------------------------------------------------------+
//| Processa comandos recebidos via CommandSocket                    |
//+------------------------------------------------------------------+
void CheckIncomingCommands()
{
   ZmqMsg zmq_msg;
   while(command_socket.recv(zmq_msg, ZMQ_DONTWAIT))
   {
      string message_str = zmq_msg.getData();
      if(InpDebugLog)
         PrintFormat("RX (Command): %s", message_str);
      JSONNode json_parser;
      if(json_parser.Deserialize(message_str))
      {
         ProcessCommand(json_parser);
      }
      else
      {
         Print("ERROR: Falha ao deserializar JSON: ", message_str);
      }
   }
}

//+------------------------------------------------------------------+
//| Processa comando JSON                                           |
//+------------------------------------------------------------------+
void ProcessCommand(JSONNode &json_command)
{
   JSONNode *cmd_node_ptr = json_command["command"];
   JSONNode *reqid_node_ptr = json_command["request_id"];
   if(CheckPointer(cmd_node_ptr) == POINTER_INVALID || CheckPointer(reqid_node_ptr) == POINTER_INVALID)
   {
      SendErrorResponse("", "Comando sem 'command' ou 'request_id'");
      return;
   }

   string command = cmd_node_ptr.ToString();
   string request_id = reqid_node_ptr.ToString();
   JSONNode *payload_node_ptr = json_command["payload"];
   JSONNode payload = (CheckPointer(payload_node_ptr) != POINTER_INVALID) ? *payload_node_ptr : JSONNode();

   // ── Comandos Admin (MASTER + SLAVE) ──
   if(command == "PING")
   {
      HandlePingCommand(request_id, payload_node_ptr);
   }
   else if(command == "GET_STATUS_INFO")
   {
      HandleGetStatusInfoCommand(request_id, payload_node_ptr);
   }
   else if(command == "GET_ACCOUNT_BALANCE")
   {
      HandleGetAccountBalanceCommand(request_id);
   }
   else if(command == "GET_ACCOUNT_FLAGS")
   {
      HandleGetAccountFlagsCommand(request_id);
   }
   else if(command == "GET_ACCOUNT_MARGIN")
   {
      HandleGetAccountMarginCommand(request_id);
   }
   else if(command == "GET_ACCOUNT_MODE")
   {
      HandleGetAccountModeCommand(request_id);
   }
   else if(command == "GET_SYMBOL_INFO")
   {
      HandleGetSymbolInfoCommand(request_id, payload);
   }
   else if(command == "SET_HEARTBEAT_INTERVAL")
   {
      HandleSetHeartbeatIntervalCommand(request_id, payload);
   }
   else if(command == "SET_MAGIC_NUMBER")
   {
      HandleSetMagicNumberCommand(request_id, payload);
   }
   else if(command == "POSITIONS" || command == "GET_POSITIONS")
   {
      HandleGetPositionsCommand(request_id);
   }
   else if(command == "ORDERS")
   {
      HandleGetOrdersCommand(request_id);
   }
   else if(command == "HISTORY_TRADES")
   {
      HandleGetHistoryTradesCommand(request_id, payload);
   }
   // ── Comandos de Trade (SLAVE only - MASTER rejeita dentro dos handlers) ──
   else if(command == "TRADE_ORDER_TYPE_BUY")
   {
      HandleTradeBuyCommand(request_id, payload);
   }
   else if(command == "TRADE_ORDER_TYPE_SELL")
   {
      HandleTradeSellCommand(request_id, payload);
   }
   else if(command == "TRADE_POSITION_MODIFY")
   {
      HandleTradePositionModifyCommand(request_id, payload);
   }
   else if(command == "TRADE_POSITION_PARTIAL")
   {
      HandleTradePositionPartialCommand(request_id, payload);
   }
   else if(command == "TRADE_POSITION_CLOSE_ID")
   {
      HandleTradePositionCloseIdCommand(request_id, payload);
   }
   else if(command == "TRADE_POSITION_CLOSE")
   {
      HandleTradePositionCloseSymbolCommand(request_id, payload);
   }
   else
   {
      SendErrorResponse(request_id, "Comando desconhecido: " + command);
   }
}

//+------------------------------------------------------------------+
//| Bloco 5 - OnTradeTransaction                                    |
//| Publica TRADE_EVENT via EventSocket para o Python.               |
//| Ambos MASTER e SLAVE publicam, mas o Python só replica do MASTER.|
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
{
   // Ignora retcodes irrelevantes
   if(result.retcode == 0 || result.retcode == TRADE_RETCODE_NO_CHANGES)
      return;

   if(InpDebugLog)
   {
      PrintFormat("OnTradeTransaction - role=%s, action=%s, retcode=%d, deal=%lld, order=%lld, symbol=%s, volume=%.2f",
                  g_role, EnumToString(request.action), result.retcode,
                  result.deal, result.order, request.symbol, request.volume);
   }

   // Só envia para retcodes relevantes
   if(result.retcode != TRADE_RETCODE_DONE &&
      result.retcode != TRADE_RETCODE_REJECT &&
      result.retcode != TRADE_RETCODE_INVALID &&
      result.retcode != TRADE_RETCODE_INVALID_PRICE)
   {
      if(InpDebugLog)
         PrintFormat("Não enviando TRADE_EVENT para retcode=%d", result.retcode);
      return;
   }

   JSONNode stream_msg;
   stream_msg["type"] = "STREAM";
   stream_msg["event"] = "TRADE_EVENT";
   stream_msg["timestamp_mql"] = (long)TimeCurrent();
   stream_msg["role"] = g_role;

   // Request data - FLATTENAR para contornar bug do Copy() no Json.mqh
   // (Copy() sobrescreve m_key com "" ao atribuir JSONNode via operator=)
   stream_msg["request_action"] = (int)request.action;
   stream_msg["request_order"] = (long)request.order;
   stream_msg["request_symbol"] = request.symbol;
   stream_msg["request_volume"] = request.volume;
   stream_msg["request_price"] = request.price;
   stream_msg["request_sl"] = request.sl;
   stream_msg["request_tp"] = request.tp;
   stream_msg["request_deviation"] = (long)request.deviation;
   stream_msg["request_type"] = (int)request.type;
   stream_msg["request_type_filling"] = (int)request.type_filling;
   stream_msg["request_comment"] = request.comment;
   stream_msg["request_position"] = (long)request.position;

   // Result data - FLATTENAR para contornar bug do Copy() no Json.mqh
   stream_msg["result_retcode"] = (long)result.retcode;
   stream_msg["result_deal"] = (long)result.deal;
   stream_msg["result_order"] = (long)result.order;
   stream_msg["result_volume"] = result.volume;
   stream_msg["result_price"] = result.price;
   stream_msg["result_comment"] = result.comment;

   // Dados extras para copytrade
   // POSITION_IDENTIFIER é a chave universal que conecta abertura, parcial e fechamento.
   // Nunca muda, mesmo em NETTING com adição de volume ou reversão de posição.
   // Fonte primária: DEAL_POSITION_ID do histórico — é o único campo 100% consistente
   // em todos os cenários (abertura, parcial, fechamento total, mesmo após posição encerrada).
   long position_id = 0;
   long deal_magic = 0;  // Lido junto com position_id (evita segundo HistoryDealSelect)
   if(request.action == TRADE_ACTION_DEAL)
   {
      // 1ª tentativa: DEAL_POSITION_ID via histórico — método mais confiável
      if(result.deal > 0 && HistoryDealSelect(result.deal))
      {
         position_id = HistoryDealGetInteger(result.deal, DEAL_POSITION_ID);
         deal_magic = HistoryDealGetInteger(result.deal, DEAL_MAGIC);
      }

      // 2ª tentativa: POSITION_IDENTIFIER via posição ativa (abertura ou fechamento parcial)
      if(position_id == 0)
      {
         if(request.position > 0 && PositionSelectByTicket(request.position))
         {
            position_id = PositionGetInteger(POSITION_IDENTIFIER);
         }
         else if(request.position == 0 && PositionSelect(request.symbol))
         {
            position_id = PositionGetInteger(POSITION_IDENTIFIER);
         }
      }

      if(position_id == 0 && InpDebugLog)
         PrintFormat("WARNING: Não foi possível obter POSITION_IDENTIFIER para %s (deal=%lld, pos=%lld)",
                     request.symbol, result.deal, request.position);

      // Volume restante após fechamento (só em fechamentos)
      if(request.position > 0)
      {
         if(PositionSelectByTicket(request.position))
         {
            stream_msg["position_volume_remaining"] = PositionGetDouble(POSITION_VOLUME);
         }
         else
         {
            // Posição não existe mais = fechamento total
            stream_msg["position_volume_remaining"] = 0.0;
         }
      }
   }
   stream_msg["position_id"] = position_id;

   if(!SendJsonMessage(stream_msg, event_socket, "Event"))
   {
      Print("ERROR: Falha ao enviar TRADE_EVENT via EventSocket");
   }

   // ── Detecção de operação alienígena (apenas SLAVE, apenas trades com sucesso) ──
   // Reutiliza deal_magic já lido no HistoryDealSelect acima (sem chamada duplicada)
   if(g_magic_number > 0 && g_role == "SLAVE" && result.retcode == TRADE_RETCODE_DONE
      && result.deal > 0 && request.action == TRADE_ACTION_DEAL
      && deal_magic != g_magic_number)
   {
      string type_str = (request.type == ORDER_TYPE_BUY) ? "BUY" : "SELL";

      PrintFormat("ALIEN TRADE detectado! magic=%lld (esperado=%lld), symbol=%s, %s %.2f lotes",
                  deal_magic, g_magic_number, request.symbol, type_str, request.volume);

      JSONNode alien_msg;
      alien_msg["type"] = "STREAM";
      alien_msg["event"] = "ALIEN_TRADE";
      alien_msg["timestamp_mql"] = (long)TimeCurrent();
      alien_msg["role"] = g_role;
      alien_msg["deal"] = (long)result.deal;
      alien_msg["deal_magic"] = deal_magic;
      alien_msg["expected_magic"] = g_magic_number;
      alien_msg["symbol"] = request.symbol;
      alien_msg["volume"] = request.volume;
      alien_msg["deal_type"] = type_str;

      if(!SendJsonMessage(alien_msg, event_socket, "Event"))
         Print("ERROR: Falha ao enviar ALIEN_TRADE via EventSocket");
   }
}
