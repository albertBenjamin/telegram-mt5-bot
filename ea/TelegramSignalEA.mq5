//+------------------------------------------------------------------+
//| TelegramSignalEA.mq5                                              |
//| E5 — Polling EA que lee señales del server FastAPI y las ejecuta  |
//|                                                                    |
//| SETUP MT5 (obligatorio):                                           |
//|  Tools > Options > Expert Advisors > Allow WebRequest for:        |
//|    http://127.0.0.1:8080                                           |
//|  Compilar en MetaEditor (F7)                                       |
//|  Adjuntar a cualquier chart con el símbolo activo                  |
//|                                                                    |
//| Flujo: OnTimer (2s) → GET /api/v1/pending-signal                  |
//|         → ParseSignal → VerifyHMAC → ExecuteSignal                 |
//|         → POST /api/v1/confirm                                      |
//+------------------------------------------------------------------+
#property copyright "telegram-mt5-bot"
#property version   "1.00"
#property description "Lee señales Telegram via FastAPI (127.0.0.1:8080) y ejecuta en MT5"

//--- Inputs
input string                  InpServerURL  = "http://127.0.0.1:8080"; // Server URL
input string                  InpHMACSecret = "";                       // HMAC Secret (del .env)
input int                     InpPollSec    = 2;                        // Intervalo de polling (s)
input double                  InpLotSize    = 0.01;                     // Tamaño de lote
input int                     InpMagicNum   = 20250304;                 // Magic number
input int                     InpSlippage   = 10;                       // Slippage (puntos)
input ENUM_ORDER_TYPE_FILLING InpFilling    = ORDER_FILLING_IOC;        // Tipo de filling

//+------------------------------------------------------------------+
//| Estructura de señal parseada                                       |
//+------------------------------------------------------------------+
struct Signal {
   string signal_id;
   string timestamp;
   string raw_message;    // valor JSON-raw (escapes preservados, para HMAC)
   string source_channel;
   string action;         // "BUY" | "SELL"
   string symbol;
   string entry_type;     // "MARKET" | "RANGE" | "LIMIT"
   double entry_price;    // LIMIT: precio exacto
   double range_low;      // RANGE: extremo inferior
   double range_high;     // RANGE: extremo superior
   bool   price_null;     // entry.price era null en JSON
   bool   range_low_null; // entry.range_low era null
   bool   range_high_null;// entry.range_high era null
   double sl;
   double tps[10];
   int    tps_count;
   string hmac_sha256;
   bool   dry_run;
};

//+------------------------------------------------------------------+
//| JSON helpers                                                       |
//+------------------------------------------------------------------+

// Extrae el valor de un campo string del JSON (con soporte de escapes JSON)
string JsonGetString(const string &json, const string &key)
  {
   string search = "\"" + key + "\":\"";
   int pos = StringFind(json, search);
   if(pos < 0) return "";
   pos += StringLen(search);

   string result = "";
   bool escaped = false;
   for(int i = pos; i < StringLen(json); i++)
     {
      ushort c = StringGetCharacter(json, i);
      if(escaped)
        {
         switch(c)
           {
            case '"':  result += "\""; break;
            case '\\': result += "\\"; break;
            case '/':  result += "/";  break;
            case 'n':  result += "\n"; break;
            case 'r':  result += "\r"; break;
            case 't':  result += "\t"; break;
            default:   result += ShortToString(c); break;
           }
         escaped = false;
        }
      else if(c == '\\')
         escaped = true;
      else if(c == '"')
         break;
      else
         result += ShortToString(c);
     }
   return result;
  }

// Extrae el valor raw de un string JSON (escapes preservados tal cual).
// Necesario para reproducir el canonical JSON idéntico al de Python.
string JsonGetStringRaw(const string &json, const string &key)
  {
   string search = "\"" + key + "\":\"";
   int pos = StringFind(json, search);
   if(pos < 0) return "";
   pos += StringLen(search);

   string result = "";
   bool escaped = false;
   for(int i = pos; i < StringLen(json); i++)
     {
      ushort c = StringGetCharacter(json, i);
      if(escaped)
        {
         result += ShortToString(c);
         escaped = false;
        }
      else if(c == '\\')
        {
         result += "\\";
         escaped = true;
        }
      else if(c == '"')
         break;
      else
         result += ShortToString(c);
     }
   return result;
  }

// Extrae valor double; sets is_null=true si el valor es null en JSON
double JsonGetDoubleNullable(const string &json, const string &key, bool &is_null)
  {
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if(pos < 0) { is_null = true; return 0.0; }
   pos += StringLen(search);

   if(StringSubstr(json, pos, 4) == "null") { is_null = true; return 0.0; }

   is_null = false;
   string val = "";
   for(int i = pos; i < StringLen(json); i++)
     {
      ushort c = StringGetCharacter(json, i);
      if(c == ',' || c == '}' || c == ']' || c == ' ' || c == '\n' || c == '\r') break;
      val += ShortToString(c);
     }
   return StringToDouble(val);
  }

// Extrae valor double (no nullable)
double JsonGetDouble(const string &json, const string &key)
  {
   bool dummy;
   return JsonGetDoubleNullable(json, key, dummy);
  }

// Extrae valor booleano
bool JsonGetBool(const string &json, const string &key)
  {
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if(pos < 0) return false;
   pos += StringLen(search);
   return StringSubstr(json, pos, 4) == "true";
  }

// Extrae subobjeto JSON (entre llaves)
string JsonGetObject(const string &json, const string &key)
  {
   string search = "\"" + key + "\":{";
   int pos = StringFind(json, search);
   if(pos < 0) return "";
   pos += StringLen(search) - 1; // apunta a '{'

   int depth = 0;
   for(int i = pos; i < StringLen(json); i++)
     {
      ushort c = StringGetCharacter(json, i);
      if(c == '{') depth++;
      else if(c == '}')
        {
         depth--;
         if(depth == 0) return StringSubstr(json, pos, i - pos + 1);
        }
     }
   return "";
  }

// Extrae array de doubles
int JsonGetDoubleArray(const string &json, const string &key, double &arr[], int max_size)
  {
   string search = "\"" + key + "\":[";
   int pos = StringFind(json, search);
   if(pos < 0) return 0;
   pos += StringLen(search);

   int end = StringFind(json, "]", pos);
   if(end < 0) return 0;

   string arr_str = StringSubstr(json, pos, end - pos);
   int count = 0, i = 0, arr_len = StringLen(arr_str);

   while(i < arr_len && count < max_size)
     {
      while(i < arr_len && StringGetCharacter(arr_str, i) == ' ') i++;
      if(i >= arr_len) break;

      string num = "";
      while(i < arr_len)
        {
         ushort c = StringGetCharacter(arr_str, i);
         if(c == ',') { i++; break; }
         num += ShortToString(c);
         i++;
        }
      StringTrimRight(num);
      StringTrimLeft(num);
      if(StringLen(num) > 0)
         arr[count++] = StringToDouble(num);
     }
   return count;
  }

//+------------------------------------------------------------------+
//| Formateo de double compatible con Python json.dumps               |
//| Python usa la representación más corta que identifica el float.   |
//+------------------------------------------------------------------+
string FormatJsonDouble(double val)
  {
   // Entero exacto → agregar .0 (igual que Python: json.dumps(5179.0) → "5179.0")
   long rounded = (long)MathRound(val);
   if(MathAbs(val - (double)rounded) < 1e-9)
      return IntegerToString(rounded) + ".0";

   // Buscar representación más corta que round-tripee correctamente
   for(int d = 1; d <= 15; d++)
     {
      string s = DoubleToString(val, d);
      if(StringToDouble(s) == val)
        {
         // Quitar trailing zeros manteniendo al menos 1 decimal
         if(StringFind(s, ".") >= 0)
           {
            while(StringLen(s) > 2 &&
                  StringGetCharacter(s, StringLen(s)-1) == '0' &&
                  StringGetCharacter(s, StringLen(s)-2) != '.')
               s = StringSubstr(s, 0, StringLen(s)-1);
           }
         return s;
        }
     }
   return DoubleToString(val, 8);
  }

//+------------------------------------------------------------------+
//| Construir JSON canónico para verificación HMAC                     |
//| Debe coincidir exactamente con Python:                             |
//|   json.dumps(payload, sort_keys=True, separators=(',',':'))        |
//|                                                                    |
//| Campos en orden alfabético (sin hmac_sha256):                     |
//|   action, dry_run, entry{price,range_high,range_low,type},        |
//|   raw_message, signal_id, sl, source_channel, symbol, timestamp,  |
//|   tps                                                              |
//+------------------------------------------------------------------+
string BuildCanonicalJson(const Signal &sig)
  {
   // entry fields en orden alfabético: price, range_high, range_low, type
   string f_price    = sig.price_null      ? "null" : FormatJsonDouble(sig.entry_price);
   string f_rng_high = sig.range_high_null ? "null" : FormatJsonDouble(sig.range_high);
   string f_rng_low  = sig.range_low_null  ? "null" : FormatJsonDouble(sig.range_low);

   string entry_json = "{\"price\":"      + f_price    +
                       ",\"range_high\":" + f_rng_high +
                       ",\"range_low\":"  + f_rng_low  +
                       ",\"type\":\""     + sig.entry_type + "\"}";

   // tps array
   string tps_str = "[";
   for(int i = 0; i < sig.tps_count; i++)
     {
      if(i > 0) tps_str += ",";
      tps_str += FormatJsonDouble(sig.tps[i]);
     }
   tps_str += "]";

   // raw_message: sig.raw_message ya contiene los escapes JSON preservados
   return "{\"action\":\""        + sig.action         + "\"" +
          ",\"dry_run\":"          + (sig.dry_run ? "true" : "false") +
          ",\"entry\":"            + entry_json         +
          ",\"raw_message\":\""    + sig.raw_message    + "\"" +
          ",\"signal_id\":\""      + sig.signal_id      + "\"" +
          ",\"sl\":"               + FormatJsonDouble(sig.sl) +
          ",\"source_channel\":\"" + sig.source_channel + "\"" +
          ",\"symbol\":\""         + sig.symbol         + "\"" +
          ",\"timestamp\":\""      + sig.timestamp      + "\"" +
          ",\"tps\":"              + tps_str            + "}";
  }

//+------------------------------------------------------------------+
//| Conversión de bytes a hex string (lowercase)                       |
//+------------------------------------------------------------------+
string BytesToHex(const uchar &bytes[])
  {
   static string digits = "0123456789abcdef";
   string hex = "";
   for(int i = 0; i < ArraySize(bytes); i++)
     {
      hex += ShortToString(StringGetCharacter(digits, bytes[i] >> 4));
      hex += ShortToString(StringGetCharacter(digits, bytes[i] & 0x0F));
     }
   return hex;
  }

//+------------------------------------------------------------------+
//| HMAC-SHA256 manual                                                 |
//| MQL5 solo tiene SHA256 puro; HMAC se construye manualmente:        |
//|   HMAC(K,m) = SHA256((K⊕opad) || SHA256((K⊕ipad) || m))          |
//+------------------------------------------------------------------+
bool HmacSha256(const string &secret, const string &message, string &result_hex)
  {
   const int BLOCK_SIZE = 64;

   uchar key_bytes[], msg_bytes[], empty[];

   StringToCharArray(secret,  key_bytes, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(key_bytes, ArraySize(key_bytes) - 1); // quitar null terminator

   StringToCharArray(message, msg_bytes, 0, WHOLE_ARRAY, CP_UTF8);
   int msg_len = ArraySize(msg_bytes) - 1;
   ArrayResize(msg_bytes, msg_len);

   // Step 1: si key > BLOCK_SIZE, hashear la key
   uchar k[];
   if(ArraySize(key_bytes) > BLOCK_SIZE)
     {
      if(CryptEncode(CRYPT_HASH_SHA256, key_bytes, empty, k) <= 0) return false;
     }
   else
      ArrayCopy(k, key_bytes);

   // Pad key a BLOCK_SIZE con ceros
   int k_len = ArraySize(k);
   ArrayResize(k, BLOCK_SIZE);
   for(int i = k_len; i < BLOCK_SIZE; i++) k[i] = 0;

   // Step 2: ipad y opad
   uchar ipad[], opad[];
   ArrayResize(ipad, BLOCK_SIZE);
   ArrayResize(opad, BLOCK_SIZE);
   for(int i = 0; i < BLOCK_SIZE; i++)
     {
      ipad[i] = (uchar)(k[i] ^ 0x36);
      opad[i] = (uchar)(k[i] ^ 0x5C);
     }

   // Step 3: inner_hash = SHA256(ipad || message)
   uchar inner_data[];
   ArrayResize(inner_data, BLOCK_SIZE + msg_len);
   ArrayCopy(inner_data, ipad,      0,          0, BLOCK_SIZE);
   ArrayCopy(inner_data, msg_bytes, BLOCK_SIZE, 0, msg_len);

   uchar inner_hash[];
   if(CryptEncode(CRYPT_HASH_SHA256, inner_data, empty, inner_hash) <= 0) return false;

   // Step 4: outer_hash = SHA256(opad || inner_hash)
   int hash_len = ArraySize(inner_hash);
   uchar outer_data[];
   ArrayResize(outer_data, BLOCK_SIZE + hash_len);
   ArrayCopy(outer_data, opad,       0,          0, BLOCK_SIZE);
   ArrayCopy(outer_data, inner_hash, BLOCK_SIZE, 0, hash_len);

   uchar outer_hash[];
   if(CryptEncode(CRYPT_HASH_SHA256, outer_data, empty, outer_hash) <= 0) return false;

   result_hex = BytesToHex(outer_hash);
   return true;
  }

//+------------------------------------------------------------------+
//| Verificar HMAC de la señal (fail-closed)                           |
//+------------------------------------------------------------------+
bool VerifyHMAC(const Signal &sig)
  {
   if(StringLen(InpHMACSecret) == 0) return true; // sin secret configurado

   string canonical = BuildCanonicalJson(sig);
   string computed;
   if(!HmacSha256(InpHMACSecret, canonical, computed))
     {
      Print("[EA] Error calculando HMAC");
      return false;
     }

   bool valid = (computed == sig.hmac_sha256);
   if(!valid)
     {
      Print("[EA] HMAC invalido!");
      Print("[EA]   Recibido:  ", sig.hmac_sha256);
      Print("[EA]   Calculado: ", computed);
      Print("[EA]   Canonical: ", canonical);
     }
   return valid;
  }

//+------------------------------------------------------------------+
//| HTTP GET                                                            |
//+------------------------------------------------------------------+
int HttpGet(const string &url, string &response)
  {
   uchar result[], data[];
   string result_headers;

   string cookie = "", referer = "";
   ResetLastError();
   int status = WebRequest("GET", url, cookie, referer, 5000, data, 0, result, result_headers);

   // Algunas builds de MT5 devuelven código no-HTTP (ej. 1001) en lugar de -1
   // cuando la URL no está en la whitelist o hay error de conexión.
   if(status == -1 || status < 100 || status > 599)
     {
      int err = GetLastError();
      Print("[EA] GET conexion fallida: status=", status, " err=", err,
            " — Verificar whitelist MT5: http://127.0.0.1:8080");
      return -1;
     }

   response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return status;
  }

//+------------------------------------------------------------------+
//| HTTP POST                                                           |
//+------------------------------------------------------------------+
int HttpPost(const string &url, const string &body, string &response)
  {
   uchar result[], data[];
   string result_headers;

   StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(data, ArraySize(data) - 1); // quitar null terminator

   string headers = "Content-Type: application/json\r\n";
   ResetLastError();
   int status = WebRequest("POST", url, headers, 5000, data, result, result_headers);

   if(status == -1)
     {
      Print("[EA] POST error=", GetLastError(), " url=", url);
      return -1;
     }

   response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return status;
  }

//+------------------------------------------------------------------+
//| Parsear señal desde JSON                                           |
//+------------------------------------------------------------------+
bool ParseSignal(const string &json, Signal &sig)
  {
   sig.signal_id      = JsonGetString(json, "signal_id");
   sig.timestamp      = JsonGetString(json, "timestamp");
   sig.raw_message    = JsonGetStringRaw(json, "raw_message"); // raw para HMAC
   sig.source_channel = JsonGetString(json, "source_channel");
   sig.action         = JsonGetString(json, "action");
   sig.symbol         = JsonGetString(json, "symbol");
   sig.hmac_sha256    = JsonGetString(json, "hmac_sha256");
   sig.sl             = JsonGetDouble(json, "sl");
   sig.dry_run        = JsonGetBool(json, "dry_run");

   if(sig.signal_id == "" || sig.action == "" || sig.symbol == "")
     {
      Print("[EA] ParseSignal: campos obligatorios faltantes");
      return false;
     }
   if(sig.action != "BUY" && sig.action != "SELL")
     {
      Print("[EA] ParseSignal: accion invalida: ", sig.action);
      return false;
     }

   // Parsear entry object
   string entry_obj = JsonGetObject(json, "entry");
   if(entry_obj == "")
     {
      Print("[EA] ParseSignal: campo 'entry' no encontrado");
      return false;
     }

   sig.entry_type  = JsonGetString(entry_obj, "type");
   sig.entry_price = JsonGetDoubleNullable(entry_obj, "price",      sig.price_null);
   sig.range_high  = JsonGetDoubleNullable(entry_obj, "range_high", sig.range_high_null);
   sig.range_low   = JsonGetDoubleNullable(entry_obj, "range_low",  sig.range_low_null);

   if(sig.entry_type == "")
     {
      Print("[EA] ParseSignal: entry.type faltante");
      return false;
     }

   // Parsear TPs
   sig.tps_count = JsonGetDoubleArray(json, "tps", sig.tps, 10);
   if(sig.tps_count == 0)
     {
      Print("[EA] ParseSignal: no hay TPs");
      return false;
     }

   return true;
  }

//+------------------------------------------------------------------+
//| Calcular precio de entrada para la orden                           |
//| RANGE: range_high para SELL, range_low para BUY (igual que parser)|
//+------------------------------------------------------------------+
double GetEntryPrice(const Signal &sig, double ask, double bid)
  {
   if(sig.entry_type == "MARKET") return (sig.action == "BUY") ? ask : bid;
   if(sig.entry_type == "LIMIT")  return sig.entry_price;
   // RANGE: punto medio del rango para ambas direcciones
   return (sig.range_high + sig.range_low) / 2.0;
  }

//+------------------------------------------------------------------+
//| Ejecutar orden en MT5                                              |
//+------------------------------------------------------------------+
bool ExecuteSignal(const Signal &sig, long &ticket)
  {
   ticket = -1;
   // Fix 1: usar el símbolo del gráfico (ej. XAUUSD-STD) en lugar del payload (ej. XAUUSD).
   // El HMAC sigue verificándose contra sig.symbol del payload — no cambia BuildCanonicalJson.
   string chart_symbol = Symbol();

   if(!SymbolSelect(chart_symbol, true))
     {
      Print("[EA] Error seleccionando simbolo: ", chart_symbol);
      return false;
     }

   int    digits = (int)SymbolInfoInteger(chart_symbol, SYMBOL_DIGITS);
   double ask    = SymbolInfoDouble(chart_symbol, SYMBOL_ASK);
   double bid    = SymbolInfoDouble(chart_symbol, SYMBOL_BID);
   double price  = NormalizeDouble(GetEntryPrice(sig, ask, bid), digits);
   double sl     = NormalizeDouble(sig.sl, digits);

   // DRY_RUN: loguear todas las órdenes sin enviar
   if(sig.dry_run)
     {
      for(int i = 0; i < sig.tps_count; i++)
        {
         double tp = NormalizeDouble(sig.tps[i], digits);
         Print("[EA] DRY_RUN | ", sig.action, " ", chart_symbol,
               " (payload:", sig.symbol, ")",
               " @ ", price, " SL=", sl, " TP", (i + 1), "=", tp,
               " id=", sig.signal_id);
        }
      ticket = 0;
      return true;
     }

   // Determinar tipo de orden y acción
   ENUM_ORDER_TYPE            order_type;
   ENUM_TRADE_REQUEST_ACTIONS action_type;

   if(sig.entry_type == "MARKET")
     {
      order_type  = (sig.action == "BUY") ? ORDER_TYPE_BUY  : ORDER_TYPE_SELL;
      action_type = TRADE_ACTION_DEAL;
     }
   else
     {
      // LIMIT y RANGE: orden pendiente
      order_type  = (sig.action == "BUY") ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;
      action_type = TRADE_ACTION_PENDING;
     }

   // Enviar una orden por cada TP
   int  ok_count    = 0;
   long first_ticket = -1;

   for(int i = 0; i < sig.tps_count; i++)
     {
      double tp = NormalizeDouble(sig.tps[i], digits);

      MqlTradeRequest req = {};
      MqlTradeResult  res = {};

      req.action       = action_type;
      req.symbol       = chart_symbol;  // Fix 1: símbolo del gráfico
      req.volume       = InpLotSize;
      req.type         = order_type;
      req.price        = price;
      req.sl           = sl;
      req.tp           = tp;
      req.deviation    = InpSlippage;
      req.magic        = InpMagicNum;
      req.comment      = "TG:" + StringSubstr(sig.signal_id, 0, 8) + "#" + IntegerToString(i + 1);
      // Fix 2: órdenes pendientes (RANGE/LIMIT) requieren ORDER_FILLING_RETURN en Market Execution.
      // Solo las órdenes de mercado (DEAL) usan el filling configurable (InpFilling).
      req.type_filling = (action_type == TRADE_ACTION_DEAL) ? InpFilling : ORDER_FILLING_RETURN;

      if(!OrderSend(req, res))
        {
         Print("[EA] OrderSend fallo TP", (i + 1), ": retcode=", res.retcode, " comment=", res.comment);
         continue;
        }

      if(res.retcode == TRADE_RETCODE_DONE || res.retcode == TRADE_RETCODE_PLACED)
        {
         long t = (long)res.order;
         if(first_ticket < 0) first_ticket = t;
         ok_count++;
         Print("[EA] Orden OK TP", (i + 1), ": ticket=", t,
               " type=", EnumToString(order_type),
               " price=", price, " sl=", sl, " tp=", tp);
        }
      else
        {
         Print("[EA] OrderSend retcode inesperado TP", (i + 1), ": ", res.retcode, " | ", res.comment);
        }
     }

   if(ok_count > 0)
     {
      ticket = first_ticket;
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Confirmar ejecución al server                                       |
//+------------------------------------------------------------------+
void ConfirmSignal(const string &signal_id, const string &status, long ticket)
  {
   string ticket_str = (ticket > 0) ? IntegerToString(ticket) : "null";
   string body = "{\"signal_id\":\"" + signal_id + "\"," +
                 "\"status\":\"" + status + "\"," +
                 "\"order_ticket\":" + ticket_str + "}";

   string url = InpServerURL + "/api/v1/confirm";
   string response;
   int http_status = HttpPost(url, body, response);

   if(http_status == 200)
      Print("[EA] Confirmado: ", signal_id, " status=", status, " ticket=", ticket);
   else
      Print("[EA] Error en confirmacion: http_status=", http_status, " resp=", response);
  }

//+------------------------------------------------------------------+
//| Ciclo de polling principal                                          |
//+------------------------------------------------------------------+
void PollAndExecute()
  {
   string url = InpServerURL + "/api/v1/pending-signal";
   string response;
   int status = HttpGet(url, response);

   if(status == -1 || status == 204) return; // Error o cola vacía

   if(status == 503)
     {
      Print("[EA] Kill switch activo en el server");
      return;
     }
   if(status != 200)
     {
      Print("[EA] Status inesperado en pending-signal: ", status);
      return;
     }

   Signal sig;
   if(!ParseSignal(response, sig))
     {
      Print("[EA] Error parseando señal: ", response);
      return;
     }

   Print("[EA] Señal: ", sig.action, " ", sig.symbol,
         " entry_type=", sig.entry_type,
         " dry_run=", sig.dry_run,
         " id=", sig.signal_id);

   // Verificar HMAC (fail-closed: no confirmar si es inválido)
   if(StringLen(InpHMACSecret) > 0 && !VerifyHMAC(sig))
     {
      Print("[EA] Señal rechazada por HMAC invalido: ", sig.signal_id);
      return;
     }

   long ticket = -1;
   bool success = ExecuteSignal(sig, ticket);
   string confirm_status = success ? "executed" : "failed";
   ConfirmSignal(sig.signal_id, confirm_status, ticket);
  }

//+------------------------------------------------------------------+
//| EA Lifecycle                                                        |
//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetTimer(InpPollSec);
   Print("=== TelegramSignalEA v1.0 ===");
   Print("[EA] Server: ",  InpServerURL);
   Print("[EA] Simbolo: ", Symbol(), " (grafico — payload ignorado para ejecucion)");
   Print("[EA] Poll: ",    InpPollSec, "s");
   Print("[EA] Lote: ",    InpLotSize);
   Print("[EA] Magic: ",   InpMagicNum);
   Print("[EA] HMAC: ",    StringLen(InpHMACSecret) > 0 ? "ACTIVADO" : "DESACTIVADO (riesgo)");
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   Print("[EA] Detenido. Razon: ", reason);
  }

void OnTick()   { /* polling via timer, no usado */ }
void OnTimer()  { PollAndExecute(); }
//+------------------------------------------------------------------+
