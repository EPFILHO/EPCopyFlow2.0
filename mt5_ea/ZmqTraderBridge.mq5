//+------------------------------------------------------------------+
//| EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000           |
//| ZmqTraderBridge.mq5                                              |
//| MQL5 <-> Python TCP Bridge para CopyTrade                        |
//| 1 socket TCP nativo (bidirecional), Python = servidor, EA = cliente.
//| Framing: 4 bytes big-endian length + UTF-8 JSON payload.         |
//| Modo MASTER: detecta trades e publica eventos                    |
//| Modo SLAVE: executa trades recebidos do Python                   |
//+------------------------------------------------------------------+
#property copyright "EPFilho"
#property link      "epfilho73@gmail.com"
#property version   "2.01"
#property strict

#include <Json.mqh>
#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Bloco 1 - Configuração e Conexão TCP                             |
//+------------------------------------------------------------------+

//--- Parâmetros configuráveis
input int    InpTimerIntervalMs  = 1000;    // Intervalo do timer (ms)
input bool   InpDebugLog         = false;   // Ativar logs de debug
input string InpTcpHost          = "127.0.0.1"; // Host do servidor Python
input int    InpConnectTimeoutMs = 1000;    // Timeout de conexão TCP (ms)

//--- Variáveis globais
int     g_socket = INVALID_HANDLE;  // Socket TCP nativo (EA = cliente)
bool    g_is_connected = false;
ulong   g_last_reconnect_attempt = 0;      // GetTickCount64() da última tentativa
const ulong RECONNECT_INTERVAL_MS = 2000;  // Tentar reconectar a cada 2s
uchar   g_rx_buffer[];               // Buffer de leitura (acumula bytes parciais)
int     g_rx_len = 0;                // Bytes válidos em g_rx_buffer
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

//--- Magic number para identificar trades do CopyTrade (lido do config.ini no OnInit)
long g_magic_number = 0;               // 0 = não configurado (desabilita detecção de aliens)

//--- REGISTER retry (OnInit pode enviar antes do Python conectar)
bool g_register_sent = false;          // true quando REGISTER foi enviado com sucesso
int  g_register_retries = 0;           // Contador de tentativas

//--- OrderSendAsync: mapa de requests pendentes (request_id MQL5 → zmq_request_id)
//    Quando o broker responde, OnTradeTransaction recebe o resultado e envia a resposta ZMQ.
#define MAX_PENDING_REQUESTS 64
struct PendingTradeRequest {
   ulong  mql_request_id;     // request_id retornado por OrderSendAsync
   string zmq_request_id;     // request_id do Python (para enviar resposta via ZMQ)
   ulong  created_at;         // GetTickCount64() — para timeout/cleanup
   bool   is_used;            // slot ativo?
};
PendingTradeRequest g_pending_requests[MAX_PENDING_REQUESTS];

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
      else if(currentSection == "[CopyTrade]")
      {
         if(chave == "MagicNumber")
         {
            long magic = StringToInteger(valor);
            if(magic > 0)
            {
               g_magic_number = magic;
               trade.SetExpertMagicNumber((ulong)magic);
            }
         }
      }
   }
   FileClose(file_handle);

   if(InpDebugLog)
   {
      PrintFormat("Config: BrokerKey=%s, Role=%s, CommandPort=%d, EventPort=%d, MagicNumber=%lld",
                  brokerKey, role, commandPort, eventPort, g_magic_number);
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
//| Bloco 1.1 - Camada TCP nativa (framing length-prefixed)          |
//+------------------------------------------------------------------+

// Forward declarations (usadas por TcpExtractAndProcessFrames)
void ProcessCommand(JSONNode &json_command);

//--- Serializa uint32 big-endian nos primeiros 4 bytes do buffer
void WriteBigEndianUint32(uchar &buffer[], int offset, uint value)
{
   buffer[offset + 0] = (uchar)((value >> 24) & 0xFF);
   buffer[offset + 1] = (uchar)((value >> 16) & 0xFF);
   buffer[offset + 2] = (uchar)((value >> 8)  & 0xFF);
   buffer[offset + 3] = (uchar)(value & 0xFF);
}

uint ReadBigEndianUint32(const uchar &buffer[], int offset)
{
   uint b0 = (uint)buffer[offset + 0];
   uint b1 = (uint)buffer[offset + 1];
   uint b2 = (uint)buffer[offset + 2];
   uint b3 = (uint)buffer[offset + 3];
   return (b0 << 24) | (b1 << 16) | (b2 << 8) | b3;
}

//--- Conecta ao servidor Python. Retorna true em sucesso.
bool TcpConnect()
{
   if(g_socket != INVALID_HANDLE)
   {
      SocketClose(g_socket);
      g_socket = INVALID_HANDLE;
   }

   g_socket = SocketCreate();
   if(g_socket == INVALID_HANDLE)
   {
      PrintFormat("ERROR: SocketCreate falhou. GetLastError()=%d", GetLastError());
      return false;
   }

   if(!SocketConnect(g_socket, InpTcpHost, g_commandPort, InpConnectTimeoutMs))
   {
      if(InpDebugLog)
         PrintFormat("TCP: SocketConnect(%s:%d) falhou. GetLastError()=%d",
                     InpTcpHost, g_commandPort, GetLastError());
      SocketClose(g_socket);
      g_socket = INVALID_HANDLE;
      return false;
   }

   g_is_connected = true;
   g_rx_len = 0;
   ArrayResize(g_rx_buffer, 65536);
   PrintFormat("TCP conectado ao servidor Python em %s:%d", InpTcpHost, g_commandPort);
   return true;
}

//--- Fecha socket TCP
void TcpDisconnect()
{
   if(g_socket != INVALID_HANDLE)
   {
      SocketClose(g_socket);
      g_socket = INVALID_HANDLE;
   }
   g_is_connected = false;
   g_rx_len = 0;
}

//--- Envia um frame [length BE][payload] pelo socket. Retorna true se todos os bytes foram enviados.
bool TcpSendFrame(const string payload)
{
   if(!g_is_connected || g_socket == INVALID_HANDLE)
      return false;

   uchar payload_bytes[];
   int payload_len = StringToCharArray(payload, payload_bytes, 0, -1, CP_UTF8);
   // StringToCharArray inclui null terminator se usado com -1; remover
   if(payload_len > 0 && payload_bytes[payload_len - 1] == 0)
      payload_len--;

   if(payload_len <= 0)
      return false;

   uchar frame[];
   ArrayResize(frame, 4 + payload_len);
   WriteBigEndianUint32(frame, 0, (uint)payload_len);
   for(int i = 0; i < payload_len; i++)
      frame[4 + i] = payload_bytes[i];

   int total = 4 + payload_len;
   int sent_total = 0;
   while(sent_total < total)
   {
      uchar chunk[];
      int remaining = total - sent_total;
      ArrayResize(chunk, remaining);
      for(int i = 0; i < remaining; i++)
         chunk[i] = frame[sent_total + i];

      int sent = SocketSend(g_socket, chunk, remaining);
      if(sent <= 0)
      {
         PrintFormat("ERROR: SocketSend falhou (sent=%d). GetLastError()=%d", sent, GetLastError());
         TcpDisconnect();
         return false;
      }
      sent_total += sent;
   }
   return true;
}

//--- Lê quantos bytes estiverem disponíveis para o buffer de RX acumulado.
void TcpPumpReads()
{
   if(!g_is_connected || g_socket == INVALID_HANDLE)
      return;

   uint available = SocketIsReadable(g_socket);
   if(available == 0)
      return;

   // Garante capacidade no buffer
   int needed = g_rx_len + (int)available;
   if(ArraySize(g_rx_buffer) < needed)
      ArrayResize(g_rx_buffer, needed + 4096);

   uchar tmp[];
   ArrayResize(tmp, (int)available);
   int read = SocketRead(g_socket, tmp, available, 100);
   if(read <= 0)
   {
      // Possivelmente desconectado
      if(!SocketIsConnected(g_socket))
      {
         Print("TCP: Conexão perdida durante leitura.");
         TcpDisconnect();
      }
      return;
   }
   for(int i = 0; i < read; i++)
      g_rx_buffer[g_rx_len + i] = tmp[i];
   g_rx_len += read;
}

//--- Extrai frames completos do buffer RX. Retorna JSONs para o callback.
void TcpExtractAndProcessFrames()
{
   while(g_rx_len >= 4)
   {
      uint payload_len = ReadBigEndianUint32(g_rx_buffer, 0);
      if(payload_len == 0 || payload_len > 16777216)  // 16 MiB cap
      {
         PrintFormat("ERROR: Frame length inválido (%u). Fechando conexão.", payload_len);
         TcpDisconnect();
         return;
      }

      int frame_size = 4 + (int)payload_len;
      if(g_rx_len < frame_size)
         return;  // frame incompleto, aguarda mais bytes

      // Extrai JSON
      uchar payload_bytes[];
      ArrayResize(payload_bytes, (int)payload_len + 1);
      for(int i = 0; i < (int)payload_len; i++)
         payload_bytes[i] = g_rx_buffer[4 + i];
      payload_bytes[payload_len] = 0;

      string message_str = CharArrayToString(payload_bytes, 0, (int)payload_len, CP_UTF8);

      // Remove frame do buffer: shift bytes restantes
      int remaining = g_rx_len - frame_size;
      for(int i = 0; i < remaining; i++)
         g_rx_buffer[i] = g_rx_buffer[frame_size + i];
      g_rx_len = remaining;

      // Processa o comando
      if(InpDebugLog)
         PrintFormat("RX: %s", message_str);

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
//| Enviar mensagem JSON pelo socket TCP (único, bidirecional)       |
//+------------------------------------------------------------------+
bool SendJsonMessage(JSONNode &json_message, string tag="TX")
{
   json_message["broker_key"] = g_brokerKey;
   if(!g_is_connected)
   {
      if(InpDebugLog)
         Print("WARN: Tentativa de envio sem conexão em ", tag);
      return false;
   }
   string message_str;
   RobustJsonSerialize(json_message, message_str);
   if(InpDebugLog)
      Print("TX (", tag, "): ", message_str);

   if(!TcpSendFrame(message_str))
   {
      PrintFormat("WARN: TcpSendFrame falhou em %s", tag);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Gerenciamento de requests pendentes (OrderSendAsync)             |
//+------------------------------------------------------------------+
void InitPendingRequests()
{
   for(int i = 0; i < MAX_PENDING_REQUESTS; i++)
      g_pending_requests[i].is_used = false;
}

bool AddPendingRequest(ulong mql_request_id, string zmq_request_id)
{
   for(int i = 0; i < MAX_PENDING_REQUESTS; i++)
   {
      if(!g_pending_requests[i].is_used)
      {
         g_pending_requests[i].mql_request_id = mql_request_id;
         g_pending_requests[i].zmq_request_id = zmq_request_id;
         g_pending_requests[i].created_at = GetTickCount64();
         g_pending_requests[i].is_used = true;
         return true;
      }
   }
   Print("WARN: Tabela de pending requests cheia (", MAX_PENDING_REQUESTS, ")");
   return false;
}

string FindAndRemovePendingRequest(ulong mql_request_id)
{
   for(int i = 0; i < MAX_PENDING_REQUESTS; i++)
   {
      if(g_pending_requests[i].is_used && g_pending_requests[i].mql_request_id == mql_request_id)
      {
         string zmq_id = g_pending_requests[i].zmq_request_id;
         g_pending_requests[i].is_used = false;
         return zmq_id;
      }
   }
   return "";
}

void CleanupStalePendingRequests()
{
   // Remove requests com mais de 30 segundos (timeout de segurança)
   ulong now = GetTickCount64();
   for(int i = 0; i < MAX_PENDING_REQUESTS; i++)
   {
      if(g_pending_requests[i].is_used && (now - g_pending_requests[i].created_at) > 30000)
      {
         PrintFormat("WARN: Pending request expirado: zmq_id=%s, mql_id=%llu",
                     g_pending_requests[i].zmq_request_id, g_pending_requests[i].mql_request_id);
         // Envia timeout para o Python
         SendErrorResponse(g_pending_requests[i].zmq_request_id, "Trade timeout (30s) no EA");
         g_pending_requests[i].is_used = false;
      }
   }
}

//+------------------------------------------------------------------+
//| Determina filling mode suportado pelo símbolo                   |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetSymbolFillingMode(string symbol)
{
   long filling_mode = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((filling_mode & SYMBOL_FILLING_FOK) != 0)
      return ORDER_FILLING_FOK;
   if((filling_mode & SYMBOL_FILLING_IOC) != 0)
      return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
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
   return SendJsonMessage(message, "Command");
}

bool SendUnregisterMessage()
{
   if(!g_is_connected) return false;
   JSONNode message;
   message["type"] = "SYSTEM";
   message["event"] = "UNREGISTER";
   message["timestamp_mql"] = (long)TimeCurrent();
   PrintFormat("Enviando UNREGISTER para %s", g_brokerKey);
   return SendJsonMessage(message, "Command");
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
   return SendJsonMessage(response, "Command");
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
   SendJsonMessage(response, "Command");
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
   SendJsonMessage(response, "Command");
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
   SendJsonMessage(response, "Command");
}

void HandleGetAccountFlagsCommand(const string request_id)
{
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["trade_allowed"] = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   response["expert_enabled"] = (bool)AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
   SendJsonMessage(response, "Command");
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
   SendJsonMessage(response, "Command");
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

   SendJsonMessage(response, "Command");
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
      SendJsonMessage(response, "Command");
      return;
   }

   string symbol = symbol_node.ToString();

   // Verificar se símbolo existe
   if(!SymbolSelect(symbol, true))
   {
      response["status"] = "ERROR";
      response["error_message"] = StringFormat("Symbol not found: %s", symbol);
      SendJsonMessage(response, "Command");
      return;
   }

   response["status"] = "OK";
   response["symbol"] = symbol;
   response["volume_min"] = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   response["volume_max"] = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   response["volume_step"] = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   response["digits"] = (long)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   response["trade_mode"] = (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);

   SendJsonMessage(response, "Command");
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
   SendJsonMessage(response, "Command");
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
   SendJsonMessage(response, "Command");
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
   SendJsonMessage(response, "Command");
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

   if(price <= 0)
      price = SymbolInfoDouble(symbol, SYMBOL_ASK);

   MqlTradeRequest req;
   MqlTradeResult res;
   ZeroMemory(req);
   ZeroMemory(res);

   req.action = TRADE_ACTION_DEAL;
   req.symbol = symbol;
   req.volume = volume;
   req.type = ORDER_TYPE_BUY;
   req.price = price;
   req.sl = sl;
   req.tp = tp;
   req.deviation = (ulong)deviation;
   req.magic = (ulong)g_magic_number;
   req.comment = comment;
   req.type_filling = GetSymbolFillingMode(symbol);

   if(!OrderSendAsync(req, res))
   {
      SendErrorResponse(request_id, StringFormat("OrderSendAsync BUY falhou: retcode=%d, %s",
                        res.retcode, res.comment));
      return;
   }

   if(!AddPendingRequest((ulong)res.request_id, request_id))
   {
      SendErrorResponse(request_id, "Pending requests table full");
      return;
   }

   if(InpDebugLog)
      PrintFormat("OrderSendAsync BUY: symbol=%s, vol=%.2f, mql_req=%u, zmq_req=%s",
                  symbol, volume, res.request_id, request_id);
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

   if(price <= 0)
      price = SymbolInfoDouble(symbol, SYMBOL_BID);

   MqlTradeRequest req;
   MqlTradeResult res;
   ZeroMemory(req);
   ZeroMemory(res);

   req.action = TRADE_ACTION_DEAL;
   req.symbol = symbol;
   req.volume = volume;
   req.type = ORDER_TYPE_SELL;
   req.price = price;
   req.sl = sl;
   req.tp = tp;
   req.deviation = (ulong)deviation;
   req.magic = (ulong)g_magic_number;
   req.comment = comment;
   req.type_filling = GetSymbolFillingMode(symbol);

   if(!OrderSendAsync(req, res))
   {
      SendErrorResponse(request_id, StringFormat("OrderSendAsync SELL falhou: retcode=%d, %s",
                        res.retcode, res.comment));
      return;
   }

   if(!AddPendingRequest((ulong)res.request_id, request_id))
   {
      SendErrorResponse(request_id, "Pending requests table full");
      return;
   }

   if(InpDebugLog)
      PrintFormat("OrderSendAsync SELL: symbol=%s, vol=%.2f, mql_req=%u, zmq_req=%s",
                  symbol, volume, res.request_id, request_id);
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
   SendJsonMessage(response, "Command");
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

   string symbol = PositionGetString(POSITION_SYMBOL);
   ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

   MqlTradeRequest req;
   MqlTradeResult res;
   ZeroMemory(req);
   ZeroMemory(res);

   req.action = TRADE_ACTION_DEAL;
   req.symbol = symbol;
   req.volume = volume;
   req.position = (ulong)ticket;
   req.type = (pos_type == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   req.price = (pos_type == POSITION_TYPE_BUY) ? SymbolInfoDouble(symbol, SYMBOL_BID) : SymbolInfoDouble(symbol, SYMBOL_ASK);
   req.deviation = 100;
   req.magic = (ulong)g_magic_number;
   req.type_filling = GetSymbolFillingMode(symbol);

   if(!OrderSendAsync(req, res))
   {
      SendErrorResponse(request_id, StringFormat("OrderSendAsync PARTIAL falhou: retcode=%d, %s",
                        res.retcode, res.comment));
      return;
   }

   if(!AddPendingRequest((ulong)res.request_id, request_id))
   {
      SendErrorResponse(request_id, "Pending requests table full");
      return;
   }

   if(InpDebugLog)
      PrintFormat("OrderSendAsync PARTIAL: ticket=%lld, vol=%.2f, mql_req=%u, zmq_req=%s",
                  ticket, volume, res.request_id, request_id);
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

   if(!PositionSelectByTicket(ticket))
   {
      SendErrorResponse(request_id, "Posição não encontrada");
      return;
   }

   string symbol = PositionGetString(POSITION_SYMBOL);
   double volume = PositionGetDouble(POSITION_VOLUME);
   ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

   MqlTradeRequest req;
   MqlTradeResult res;
   ZeroMemory(req);
   ZeroMemory(res);

   req.action = TRADE_ACTION_DEAL;
   req.symbol = symbol;
   req.volume = volume;
   req.position = (ulong)ticket;
   req.type = (pos_type == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   req.price = (pos_type == POSITION_TYPE_BUY) ? SymbolInfoDouble(symbol, SYMBOL_BID) : SymbolInfoDouble(symbol, SYMBOL_ASK);
   req.deviation = 100;
   req.magic = (ulong)g_magic_number;
   req.type_filling = GetSymbolFillingMode(symbol);

   if(!OrderSendAsync(req, res))
   {
      SendErrorResponse(request_id, StringFormat("OrderSendAsync CLOSE falhou: retcode=%d, %s",
                        res.retcode, res.comment));
      return;
   }

   if(!AddPendingRequest((ulong)res.request_id, request_id))
   {
      SendErrorResponse(request_id, "Pending requests table full");
      return;
   }

   if(InpDebugLog)
      PrintFormat("OrderSendAsync CLOSE: ticket=%lld, symbol=%s, mql_req=%u, zmq_req=%s",
                  ticket, symbol, res.request_id, request_id);
}

void HandleTradePositionCloseSymbolCommand(const string request_id, JSONNode &payload)
{
   if(g_role == "MASTER")
   {
      SendErrorResponse(request_id, "MASTER não aceita comandos de trade");
      return;
   }

   string symbol = payload["symbol"].ToString();
   int submitted = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket) && PositionGetString(POSITION_SYMBOL) == symbol)
      {
         double volume = PositionGetDouble(POSITION_VOLUME);
         ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

         MqlTradeRequest req;
         MqlTradeResult res;
         ZeroMemory(req);
         ZeroMemory(res);

         req.action = TRADE_ACTION_DEAL;
         req.symbol = symbol;
         req.volume = volume;
         req.position = ticket;
         req.type = (pos_type == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
         req.price = (pos_type == POSITION_TYPE_BUY) ? SymbolInfoDouble(symbol, SYMBOL_BID) : SymbolInfoDouble(symbol, SYMBOL_ASK);
         req.deviation = 100;
         req.magic = (ulong)g_magic_number;
         req.type_filling = GetSymbolFillingMode(symbol);

         if(OrderSendAsync(req, res))
            submitted++;
         else
            PrintFormat("WARN: OrderSendAsync CLOSE falhou para ticket=%llu: %s", ticket, res.comment);
      }
   }

   // Resposta imediata: ordens submetidas (resultados individuais via TRADE_EVENT)
   JSONNode response;
   response["type"] = "RESPONSE";
   response["request_id"] = request_id;
   response["status"] = "OK";
   response["retcode"] = (long)TRADE_RETCODE_PLACED;
   response["result"] = StringFormat("%d close requests submitted for %s", submitted, symbol);
   SendJsonMessage(response, "Command");
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

   // Conectar via TCP nativo (Python é o servidor).
   // Se a conexão falhar aqui, o OnTimer fará retry periódico.
   InitPendingRequests();
   if(TcpConnect())
   {
      if(SendRegisterMessage())
      {
         g_register_sent = true;
         g_register_retries = 0;
      }
      else
      {
         Print("REGISTER falhou no OnInit. Retry via OnTimer.");
         g_register_sent = false;
         g_register_retries = 0;
      }
   }
   else
   {
      Print("TCP connect falhou no OnInit (Python pode não estar escutando ainda). Retry via OnTimer.");
      g_register_sent = false;
      g_register_retries = 0;
   }
   g_last_reconnect_attempt = GetTickCount64();

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
   TcpDisconnect();
   Print("EPCopyFlow EA: Desinicialização completa.");
}

//+------------------------------------------------------------------+
//| OnTimer                                                          |
//+------------------------------------------------------------------+
void OnTimer()
{
   // Reconexão: se não há socket, tenta conectar periodicamente ao Python.
   if(!g_is_connected)
   {
      ulong now = GetTickCount64();
      if(now - g_last_reconnect_attempt >= RECONNECT_INTERVAL_MS)
      {
         g_last_reconnect_attempt = now;
         if(TcpConnect())
         {
            // Força reenvio de REGISTER e estados iniciais na nova sessão
            g_register_sent = false;
            g_register_retries = 0;
            g_initial_trade_allowed_sent = false;
            g_initial_connection_status_sent = false;
         }
      }
      return;
   }

   // Sanidade: verifica se o socket ainda está conectado
   if(g_socket == INVALID_HANDLE || !SocketIsConnected(g_socket))
   {
      Print("TCP: SocketIsConnected() retornou false. Desconectando para reconexão.");
      TcpDisconnect();
      return;
   }

   // Retry REGISTER se falhou (Python pode não ter aceitado ainda)
   if(!g_register_sent && g_register_retries < 30)
   {
      g_register_retries++;
      if(SendRegisterMessage())
      {
         g_register_sent = true;
         PrintFormat("REGISTER enviado com sucesso na tentativa %d.", g_register_retries);
      }
   }

   // Processa comandos recebidos via TCP
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
      SendJsonMessage(msg, "Event");
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
      SendJsonMessage(msg, "Event");
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
      SendJsonMessage(msg, "Event");
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
      SendJsonMessage(msg, "Event");
      g_last_terminal_connected = current_connected;
      if(InpDebugLog)
         PrintFormat("CONNECTION_STATUS: %s", current_connected ? "connected" : "disconnected");
   }

   // Limpar requests assíncronos expirados (timeout 30s)
   CleanupStalePendingRequests();
}

//+------------------------------------------------------------------+
//| Processa comandos recebidos via TCP                              |
//+------------------------------------------------------------------+
void CheckIncomingCommands()
{
   // Drena todos os bytes disponíveis no socket e extrai frames completos
   TcpPumpReads();
   TcpExtractAndProcessFrames();
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
   // Só processa TRADE_TRANSACTION_REQUEST — o único tipo que preenche request/result.
   // Outros tipos (DEAL_ADD, ORDER_ADD, etc.) chegam com result.retcode==0 e request zerado.
   // Filtro explícito evita debug logging e HistoryDealSelect desnecessários.
   if(trans.type != TRADE_TRANSACTION_REQUEST)
      return;

   // Ignora retcodes irrelevantes
   if(result.retcode == 0 || result.retcode == TRADE_RETCODE_NO_CHANGES)
      return;

   if(InpDebugLog)
   {
      PrintFormat("OnTradeTransaction - role=%s, action=%s, retcode=%d, deal=%lld, order=%lld, symbol=%s, volume=%.2f",
                  g_role, EnumToString(request.action), result.retcode,
                  result.deal, result.order, request.symbol, request.volume);
   }

   // ── Resposta assíncrona para OrderSendAsync pendentes ──
   // Deve ficar ANTES do filtro de retcode do TRADE_EVENT, pois precisamos responder
   // ao Python para qualquer retcode (sucesso ou erro).
   if(result.request_id > 0)
   {
      string zmq_id = FindAndRemovePendingRequest((ulong)result.request_id);
      if(zmq_id != "")
      {
         JSONNode async_response;
         async_response["type"] = "RESPONSE";
         async_response["request_id"] = zmq_id;

         if(result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED
            || result.retcode == TRADE_RETCODE_DONE_PARTIAL)
         {
            async_response["status"] = "OK";
         }
         else
         {
            async_response["status"] = "ERROR";
            async_response["error_message"] = StringFormat("Trade retcode=%d: %s", result.retcode, result.comment);
         }

         async_response["retcode"] = (long)result.retcode;
         async_response["result"] = result.comment;
         async_response["deal"] = (long)result.deal;
         async_response["order"] = (long)result.order;
         async_response["volume"] = result.volume;
         async_response["price"] = result.price;

         // ticket: posição (para close) ou deal (para abertura)
         if(request.position > 0)
            async_response["ticket"] = (long)request.position;
         else
            async_response["ticket"] = (long)result.deal;

         SendJsonMessage(async_response, "Command");

         if(InpDebugLog)
            PrintFormat("Async RESPONSE enviado: zmq_req=%s, retcode=%d, deal=%lld",
                        zmq_id, result.retcode, result.deal);
      }
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

   if(!SendJsonMessage(stream_msg, "Event"))
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

      if(!SendJsonMessage(alien_msg, "Event"))
         Print("ERROR: Falha ao enviar ALIEN_TRADE via EventSocket");
   }
}
