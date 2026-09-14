//+------------------------------------------------------------------+
//|                                                   LineaGris.mq5  |
//|   La regla de la linea gris para MetaTrader 5 (Exness y otros)   |
//+------------------------------------------------------------------+
//  La linea gris del grafico es la EMA lenta (200). Dos reglas:
//
//    LONG  : el precio viene por arriba, toca o perfora la linea y deja una
//            VELA MARTILLO que vuelve a cerrar arriba. Rechazo del nivel.
//    SHORT : el cierre pasa de largo al otro lado de la linea. Dejo de sostener.
//
//  Es el mismo calculo que tradingview/linea_gris.pine y que bot/linea_gris.py,
//  para que el grafico, el bot de Binance y este EA cuenten la misma historia.
//
//  ---------------------------------------------------------------------------
//  LO QUE DIO AL MEDIRLO. LEELO ANTES DE PONERLE PLATA.
//
//  En oro, plata, 9 pares de forex y el S&P, en H1, con 2 a 3 anios de historia
//  y costos de Exness, la regla es una APUESTA A LA DIRECCION, no una senal:
//
//      Todos esos activos SUBIERON en el periodo (oro +82%, plata +129%).
//        - short al romper hacia abajo (la regla pedida) .... -0.156 R
//        - long  al romper hacia arriba (la misma, al revés) . +0.213 R
//
//      En 25 criptos de Binance, donde 23 de 25 BAJARON, pasa lo contrario:
//        - short .... +0.030 R      - long .... -0.166 R
//
//      Y la version simetrica, que toma la ruptura para los dos lados y es la
//      unica que se puede operar sin adivinar el futuro:
//        - oro/forex/indices .... +0.011 R por operacion (638 operaciones)
//        - criptos .............. -0.088 R por operacion (553 operaciones)
//
//  O sea: cero. Gana el lado que coincidio con lo que hizo el mercado, y en
//  vivo eso no se sabe de antemano. En el oro puntualmente, la regla tal como
//  la pediste (short en la ruptura) dio -0.383 R por operacion, porque el oro
//  estuvo subiendo todo el periodo.
//
//  Lo que SI es una ventaja real de operar aca en vez de Binance: el costo. En
//  Binance futuros la ida y vuelta cuesta ~0.10% del nocional; en oro con
//  spread de 0.20 USD sobre 4300 es ~0.005%, y en EURUSD con 1 pip ~0.009%.
//  Entre 10 y 20 veces mas barato. Eso permite usar stops cortos sin que la
//  comision se los coma, que es lo que en Binance no se podia.
//
//  Por eso el EA arranca con SoloVisual = true: dibuja las senales y no opera.
//  Antes de sacarle ese candado, corrélo en el Probador de Estrategias con
//  "Every tick based on real ticks" sobre TU cuenta de Exness, que incluye tu
//  spread, tu comision y tu swap reales.
//  ---------------------------------------------------------------------------
#property copyright "Linea Gris"
#property version   "1.00"
#property description "Martillo en la EMA 200 y ruptura de la linea. Arranca en modo visual."
#property strict

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

//--- Enumeracion para el modo de operacion
enum ModoDireccion
  {
   AMBOS_LADOS,      // Ruptura para los dos lados (lo unico honesto)
   SOLO_SHORT,       // Solo short al romper hacia abajo (la regla pedida)
   SOLO_LONG         // Solo long al romper hacia arriba (el espejo)
  };

//--- ① La linea
input group                "① La linea gris"
input int      EmaPeriodo        = 200;      // EMA - periodo (200 = la linea gris)
input ENUM_MA_METHOD MetodoMedia = MODE_EMA; // Tipo de media
input int      AtrPeriodo        = 14;       // ATR - periodo
input bool     ExigirPendiente   = true;     // Exigir que la linea vaya a favor
input int      PendienteVelas    = 20;       // Velas para medir la pendiente

//--- ② El martillo
input group                "② El rebote con martillo (LONG)"
input bool     UsarMartillo      = true;     // Buscar el rebote con martillo
input double   ToleranciaAtr     = 0.05;     // Cerca de la linea = ATR x
input double   MechaFraccion     = 0.60;     // Mecha >= esta parte del rango
input double   MechaMinimo       = 3.0;      // Mecha >= cuerpo x
input double   ContraMaximo      = 0.20;     // Mecha opuesta <= esta parte del rango
input bool     ExigirColor       = true;     // El martillo tiene que cerrar verde

//--- ③ La ruptura
input group                "③ La ruptura de la linea"
input bool     UsarRuptura       = true;     // Operar la ruptura
input ModoDireccion Direccion    = AMBOS_LADOS; // Que lados tomar
input double   RupturaAtr        = 0.20;     // Pasarse de la linea por ATR x

//--- ④ Gestion
input group                "④ Riesgo y objetivo"
input double   RiesgoPorcentaje  = 0.5;      // Riesgo por operacion (% del balance)
input double   ObjetivoR         = 4.0;      // Objetivo en R (1:4)
input double   ColchonAtr        = 0.15;     // Colchon del stop (ATR x)
input double   StopMinimoPct     = 0.0;      // Stop minimo (% del precio, 0 = automatico)
input double   StopMaximoPct     = 3.0;      // Stop maximo (% del precio)
input double   CostoIdaVueltaPct = 0.015;    // Tu costo ida y vuelta (% del precio)
input double   CostoMaximoR      = 0.05;     // Cuanto de 1R puede comerse el costo
input int      EsperaVelas       = 3;        // Velas de espera entre senales
input int      MaxPosiciones     = 1;        // Posiciones simultaneas de este EA

//--- ⑤ Seguridad
input group                "⑤ Seguridad"
input bool     SoloVisual        = true;     // NO operar, solo dibujar las senales
input int      SpreadMaximoPuntos = 0;       // Spread maximo permitido (0 = sin limite)
input int      DeslizamientoPuntos = 20;     // Slippage permitido
input bool     FiltrarHorario    = false;    // Operar solo en una franja horaria
input int      HoraDesde         = 7;        // Desde (hora del servidor)
input int      HoraHasta         = 20;       // Hasta (hora del servidor)
input bool     CerrarViernes     = false;    // Cerrar todo el viernes
input int      ViernesHora       = 20;       // Hora del cierre del viernes
input long     NumeroMagico      = 20260914; // Numero magico

//--- ⑥ Dibujo
input group                "⑥ Dibujo en el grafico"
input bool     DibujarSenales    = true;     // Flechas y carteles
input color    ColorAlcista      = clrMediumSpringGreen;
input color    ColorBajista      = clrTomato;

//--- Globales
CTrade         g_trade;
CSymbolInfo    g_simbolo;
int            g_ema      = INVALID_HANDLE;
int            g_atr      = INVALID_HANDLE;
datetime       g_ultimaVela = 0;
datetime       g_ultimaSenal = 0;
int            g_dibujos  = 0;

//+------------------------------------------------------------------+
//| Inicializacion                                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(!g_simbolo.Name(_Symbol))
     {
      Print("No pude tomar el simbolo ", _Symbol);
      return(INIT_FAILED);
     }
   g_simbolo.RefreshRates();

   g_ema = iMA(_Symbol, PERIOD_CURRENT, EmaPeriodo, 0, MetodoMedia, PRICE_CLOSE);
   g_atr = iATR(_Symbol, PERIOD_CURRENT, AtrPeriodo);
   if(g_ema == INVALID_HANDLE || g_atr == INVALID_HANDLE)
     {
      Print("No pude crear los indicadores");
      return(INIT_FAILED);
     }

   g_trade.SetExpertMagicNumber(NumeroMagico);
   g_trade.SetDeviationInPoints(DeslizamientoPuntos);
   g_trade.SetMarginMode();
   // Exness y cada broker aceptan modos de llenado distintos: que lo resuelva
   // la libreria mirando la especificacion del simbolo.
   g_trade.SetTypeFillingBySymbol(_Symbol);

   if(RiesgoPorcentaje <= 0.0 || RiesgoPorcentaje > 5.0)
      Print("AVISO: RiesgoPorcentaje=", RiesgoPorcentaje,
            ". Arriba de 2% por operacion es imprudente.");

   if(SoloVisual)
      Print("MODO VISUAL: dibujo las senales y NO mando ordenes. ",
            "Sacale el candado solo despues de probarlo en el Probador.");
   else
      Print("MODO REAL: voy a mandar ordenes con riesgo de ",
            RiesgoPorcentaje, "% por operacion.");

   Print("Piso de stop efectivo: ", DoubleToString(PisoStop(), 3), "% del precio",
         " (costo ", CostoIdaVueltaPct, "% / ", CostoMaximoR, " R)");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Cierre                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int razon)
  {
   if(g_ema != INVALID_HANDLE)
      IndicatorRelease(g_ema);
   if(g_atr != INVALID_HANDLE)
      IndicatorRelease(g_atr);
   if(razon == REASON_REMOVE || razon == REASON_CHARTCHANGE)
      BorrarDibujos();
  }

//+------------------------------------------------------------------+
//| El piso del stop: derivado del costo, no un numero inventado.     |
//|                                                                  |
//| La comision y el spread son un porcentaje del nocional, no de tu  |
//| riesgo. Sobre un stop del 0.1% un costo del 0.015% se lleva 0.15R;|
//| sobre uno del 0.3%, 0.05R. Este piso es justamente el stop mas    |
//| chico que deja el costo por debajo de CostoMaximoR.               |
//+------------------------------------------------------------------+
double PisoStop()
  {
   if(StopMinimoPct > 0.0)
      return(StopMinimoPct);
   if(CostoMaximoR <= 0.0)
      return(0.0);
   return(CostoIdaVueltaPct / CostoMaximoR);
  }

//+------------------------------------------------------------------+
//| Es martillo (alcista) o estrella fugaz (bajista)?                |
//+------------------------------------------------------------------+
bool EsMartillo(const double apertura, const double maximo, const double minimo,
                const double cierre, const bool alcista)
  {
   double rango = maximo - minimo;
   if(rango <= 0.0)
      return(false);

   double cuerpo = MathAbs(cierre - apertura);
   double techo  = MathMax(apertura, cierre);
   double piso   = MathMin(apertura, cierre);
   double mecha  = alcista ? (piso - minimo)  : (maximo - techo);
   double contra = alcista ? (maximo - techo) : (piso - minimo);

   if(mecha < rango * MechaFraccion)
      return(false);
   if(contra > rango * ContraMaximo)
      return(false);
   if(cuerpo > 0.0 && mecha < cuerpo * MechaMinimo)
      return(false);
   if(ExigirColor)
     {
      if(alcista && cierre < apertura)
         return(false);
      if(!alcista && cierre > apertura)
         return(false);
     }
   return(true);
  }

//+------------------------------------------------------------------+
//| Posiciones abiertas por este EA en este simbolo                  |
//+------------------------------------------------------------------+
int MisPosiciones()
  {
   int cuenta = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == NumeroMagico)
         cuenta++;
     }
   return(cuenta);
  }

//+------------------------------------------------------------------+
//| Tamano de la posicion segun el riesgo, con todos los limites      |
//| del simbolo respetados.                                           |
//+------------------------------------------------------------------+
double CalcularLote(const double entrada, const double stop)
  {
   double distancia = MathAbs(entrada - stop);
   if(distancia <= 0.0)
      return(0.0);

   double plata = AccountInfoDouble(ACCOUNT_BALANCE) * RiesgoPorcentaje / 100.0;
   if(plata <= 0.0)
      return(0.0);

   // Perdida de 1 lote si salta el stop, calculada por el propio terminal.
   double perdidaPorLote = 0.0;
   if(!OrderCalcProfit(ORDER_TYPE_BUY, _Symbol, 1.0, entrada, entrada - distancia,
                       perdidaPorLote))
     {
      // Si el terminal no puede, se cae al calculo por tick value.
      double valorTick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tamTick   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(valorTick <= 0.0 || tamTick <= 0.0)
         return(0.0);
      perdidaPorLote = -(distancia / tamTick) * valorTick;
     }
   perdidaPorLote = MathAbs(perdidaPorLote);
   if(perdidaPorLote <= 0.0)
      return(0.0);

   double lote = plata / perdidaPorLote;

   double minimo = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maximo = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double paso   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(paso <= 0.0)
      paso = minimo;

   // Se redondea PARA ABAJO: nunca arriesgar mas de lo configurado.
   lote = MathFloor(lote / paso) * paso;
   lote = NormalizeDouble(lote, 2);

   if(lote < minimo)
     {
      // El lote minimo del broker arriesgaria mas de lo permitido.
      double riesgoMinimo = minimo * perdidaPorLote;
      PrintFormat("Salteo la senal: el lote minimo (%.2f) arriesgaria %.2f de cuenta "
                  "(%.2f%%) y el limite es %.2f (%.2f%%).",
                  minimo, riesgoMinimo,
                  riesgoMinimo / AccountInfoDouble(ACCOUNT_BALANCE) * 100.0,
                  plata, RiesgoPorcentaje);
      return(0.0);
     }
   if(lote > maximo)
      lote = maximo;

   return(lote);
  }

//+------------------------------------------------------------------+
//| Respeta el minimo que exige el broker entre precio y stop         |
//+------------------------------------------------------------------+
bool StopsValidos(const bool esCompra, const double entrada,
                  double &stop, double &objetivo)
  {
   long nivel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double punto = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minimo = nivel * punto;
   int digitos = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   if(minimo > 0.0)
     {
      if(MathAbs(entrada - stop) < minimo || MathAbs(objetivo - entrada) < minimo)
        {
         PrintFormat("Salteo la senal: el broker exige %d puntos entre precio y stop "
                     "y el plan tiene %.1f.", (int)nivel,
                     MathAbs(entrada - stop) / punto);
         return(false);
        }
     }
   stop = NormalizeDouble(stop, digitos);
   objetivo = NormalizeDouble(objetivo, digitos);
   return(true);
  }

//+------------------------------------------------------------------+
//| Dibuja la senal en el grafico                                    |
//+------------------------------------------------------------------+
void DibujarSenal(const bool esCompra, const datetime momento, const double precio,
                  const string texto)
  {
   if(!DibujarSenales)
      return;

   string base = StringFormat("LG_%d_%d", NumeroMagico, g_dibujos++);
   string flecha = base + "_f";
   if(ObjectCreate(0, flecha, OBJ_ARROW, 0, momento, precio))
     {
      ObjectSetInteger(0, flecha, OBJPROP_ARROWCODE, esCompra ? 233 : 234);
      ObjectSetInteger(0, flecha, OBJPROP_COLOR, esCompra ? ColorAlcista : ColorBajista);
      ObjectSetInteger(0, flecha, OBJPROP_WIDTH, 2);
      ObjectSetInteger(0, flecha, OBJPROP_ANCHOR, esCompra ? ANCHOR_TOP : ANCHOR_BOTTOM);
     }

   string cartel = base + "_t";
   if(ObjectCreate(0, cartel, OBJ_TEXT, 0, momento, precio))
     {
      ObjectSetString(0, cartel, OBJPROP_TEXT, texto);
      ObjectSetInteger(0, cartel, OBJPROP_COLOR, esCompra ? ColorAlcista : ColorBajista);
      ObjectSetInteger(0, cartel, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, cartel, OBJPROP_ANCHOR,
                       esCompra ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER);
     }
  }

//+------------------------------------------------------------------+
//| Limpia los dibujos del EA                                        |
//+------------------------------------------------------------------+
void BorrarDibujos()
  {
   string prefijo = StringFormat("LG_%d_", NumeroMagico);
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
     {
      string nombre = ObjectName(0, i);
      if(StringFind(nombre, prefijo) == 0)
         ObjectDelete(0, nombre);
     }
  }

//+------------------------------------------------------------------+
//| Filtro de horario del servidor                                   |
//+------------------------------------------------------------------+
bool HorarioPermitido()
  {
   if(!FiltrarHorario)
      return(true);
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   if(HoraDesde <= HoraHasta)
      return(t.hour >= HoraDesde && t.hour < HoraHasta);
   // Franja que cruza la medianoche.
   return(t.hour >= HoraDesde || t.hour < HoraHasta);
  }

//+------------------------------------------------------------------+
//| Cierra las posiciones del EA (para el viernes)                   |
//+------------------------------------------------------------------+
void CerrarTodo(const string motivo)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         PositionGetInteger(POSITION_MAGIC) != NumeroMagico)
         continue;
      if(g_trade.PositionClose(ticket))
         Print("Cierro ", ticket, ": ", motivo);
      else
         Print("No pude cerrar ", ticket, ": ", g_trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
//| Es viernes despues de la hora de cierre?                         |
//+------------------------------------------------------------------+
bool ViernesDeCierre()
  {
   if(!CerrarViernes)
      return(false);
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   return(t.day_of_week == FRIDAY && t.hour >= ViernesHora);
  }

//+------------------------------------------------------------------+
//| Cada tick: solo se trabaja cuando cierra una vela                |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(ViernesDeCierre())
     {
      CerrarTodo("cierre de fin de semana");
      return;
     }

   datetime velaActual = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(velaActual == 0 || velaActual == g_ultimaVela)
      return;              // todavia estamos dentro de la misma vela
   g_ultimaVela = velaActual;

   RevisarVelaCerrada();
  }

//+------------------------------------------------------------------+
//| Toda la logica, una vez por vela cerrada                         |
//+------------------------------------------------------------------+
void RevisarVelaCerrada()
  {
   int necesarias = MathMax(EmaPeriodo, MathMax(AtrPeriodo, PendienteVelas)) + 5;
   if(Bars(_Symbol, PERIOD_CURRENT) < necesarias)
      return;

   // La vela 1 es la ultima CERRADA. La 0 esta abierta y no sirve.
   // PendienteVelas + 1 valores: asi ema[0] queda exactamente PendienteVelas
   // velas antes de ema[u], igual que en la version de Python.
   double ema[];
   double atr[];
   if(CopyBuffer(g_ema, 0, 1, PendienteVelas + 1, ema) < PendienteVelas + 1)
      return;
   if(CopyBuffer(g_atr, 0, 1, 2, atr) < 2)
      return;

   MqlRates velas[];
   if(CopyRates(_Symbol, PERIOD_CURRENT, 1, 2, velas) < 2)
      return;

   // CopyBuffer y CopyRates devuelven el mas viejo primero: el ultimo elemento
   // es la vela recien cerrada.
   int u = ArraySize(ema) - 1;
   double lineaAhora  = ema[u];
   double lineaPrevia = ema[u - 1];
   double lineaAntes  = ema[0];              // PendienteVelas velas atras
   double atrAhora    = atr[ArraySize(atr) - 1];
   if(atrAhora <= 0.0)
      return;

   MqlRates v  = velas[1];                   // vela cerrada
   MqlRates vp = velas[0];                   // la anterior

   bool subiendo = lineaAhora > lineaAntes;
   double tolerancia = atrAhora * ToleranciaAtr;

   //--- Que senal hay, si hay alguna
   bool esCompra = false;
   double stop = 0.0;
   string tipo = "";

   // LONG: martillo apoyado en la linea
   if(UsarMartillo && v.close > lineaAhora && (subiendo || !ExigirPendiente))
     {
      if(v.low <= lineaAhora + tolerancia &&
         EsMartillo(v.open, v.high, v.low, v.close, true))
        {
         esCompra = true;
         stop = v.low - atrAhora * ColchonAtr;
         tipo = "martillo";
        }
     }

   // LONG por ruptura hacia arriba
   if(tipo == "" && UsarRuptura &&
      (Direccion == AMBOS_LADOS || Direccion == SOLO_LONG))
     {
      if(vp.close < lineaPrevia && v.close > lineaAhora + atrAhora * RupturaAtr)
        {
         esCompra = true;
         stop = v.low - atrAhora * ColchonAtr;
         tipo = "ruptura alta";
        }
     }

   // SHORT por ruptura hacia abajo
   if(tipo == "" && UsarRuptura &&
      (Direccion == AMBOS_LADOS || Direccion == SOLO_SHORT))
     {
      if(vp.close > lineaPrevia && v.close < lineaAhora - atrAhora * RupturaAtr)
        {
         esCompra = false;
         stop = v.high + atrAhora * ColchonAtr;
         tipo = "ruptura baja";
        }
     }

   if(tipo == "")
      return;

   //--- Filtros de riesgo
   double entrada = v.close;
   double riesgo = MathAbs(entrada - stop);
   if(riesgo <= 0.0)
      return;
   double riesgoPct = riesgo / entrada * 100.0;
   double piso = PisoStop();

   if(riesgoPct < piso)
     {
      PrintFormat("%s descartado: stop de %.3f%% y el piso por costos es %.3f%%. "
                  "El costo se comeria %.2f R.", tipo, riesgoPct, piso,
                  CostoIdaVueltaPct / riesgoPct);
      return;
     }
   if(StopMaximoPct > 0.0 && riesgoPct > StopMaximoPct)
     {
      PrintFormat("%s descartado: stop de %.3f%% supera el maximo de %.3f%%.",
                  tipo, riesgoPct, StopMaximoPct);
      return;
     }

   double objetivo = esCompra ? entrada + riesgo * ObjetivoR
                              : entrada - riesgo * ObjetivoR;

   //--- Dibujo siempre, opere o no
   string etiqueta = StringFormat("%s %s %s (SL %.2f%%)",
                                  esCompra ? "LONG" : "SHORT", tipo,
                                  DoubleToString(entrada,
                                     (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
                                  riesgoPct);
   DibujarSenal(esCompra, v.time, esCompra ? v.low : v.high, etiqueta);
   Print("SENAL ", etiqueta);

   if(SoloVisual)
      return;

   //--- Candados antes de operar
   if(g_ultimaSenal > 0 && EsperaVelas > 0)
     {
      int segundosVela = PeriodSeconds(PERIOD_CURRENT);
      if((v.time - g_ultimaSenal) < (datetime)(EsperaVelas * segundosVela))
        {
         Print("  no opero: faltan velas de espera desde la senal anterior");
         return;
        }
     }
   if(MisPosiciones() >= MaxPosiciones)
     {
      Print("  no opero: ya tengo ", MisPosiciones(), " posicion(es) abierta(s)");
      return;
     }
   if(!HorarioPermitido())
     {
      Print("  no opero: fuera del horario configurado");
      return;
     }
   if(!g_simbolo.RefreshRates())
      return;
   if(SpreadMaximoPuntos > 0)
     {
      long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      if(spread > SpreadMaximoPuntos)
        {
         Print("  no opero: spread de ", spread, " puntos supera el maximo de ",
               SpreadMaximoPuntos);
         return;
        }
     }

   if(!StopsValidos(esCompra, entrada, stop, objetivo))
      return;

   double lote = CalcularLote(entrada, stop);
   if(lote <= 0.0)
      return;

   //--- A mercado, con el SL y el TP adjuntos
   bool ok = esCompra
             ? g_trade.Buy(lote, _Symbol, 0.0, stop, objetivo, etiqueta)
             : g_trade.Sell(lote, _Symbol, 0.0, stop, objetivo, etiqueta);

   if(ok)
     {
      g_ultimaSenal = v.time;
      PrintFormat("  ORDEN %s %.2f lotes | SL %s | TP %s | riesgo %.2f%% del balance",
                  esCompra ? "BUY" : "SELL", lote,
                  DoubleToString(stop, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
                  DoubleToString(objetivo, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
                  RiesgoPorcentaje);
     }
   else
      PrintFormat("  RECHAZADA: %d %s", g_trade.ResultRetcode(),
                  g_trade.ResultRetcodeDescription());
  }
//+------------------------------------------------------------------+
