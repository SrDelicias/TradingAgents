# Paper trading local — Fase 2

## Alcance y seguridad

Esta capa convierte la decisión final del Portfolio Manager en operaciones **exclusivamente ficticias** sobre una cartera JSON local. No contiene claves, SDKs ni endpoints de brokers o exchanges y no puede enviar órdenes reales.

Invariantes:

- capital inicial: 50 EUR;
- solo efectivo disponible, sin margen, apalancamiento, cortos ni saldos negativos;
- las compras solo pasan al `PaperBroker` tras ser aprobadas por el `HardRiskEngine`;
- los precios obsoletos bloquean aumentos de exposición;
- un fallo de precio conserva el último precio conocido, marca la posición como `price_stale` y no inventa una cotización;
- stop loss, take profit y trailing stop son reglas deterministas, sin intervención de un LLM.

## Ciclo de cada ejecución

```text
Portfolio Manager
        |
        v
Load VirtualPortfolio
        |
        v
Mark-to-market de TODAS las posiciones
        |
        v
Salidas automáticas (stop / take profit / trailing)
        |
        v
Normalización de la decisión del PM
        |
        v
HardRiskEngine
        |
        v
PaperBroker (fill + costes simulados)
        |
        v
Guardado atómico de cartera + snapshot de equity
        |
        v
Informe CLI: PAPER PORTFOLIO + LAST EXECUTION
```

Una salida automática del símbolo analizado tiene prioridad sobre la recomendación del PM del mismo ciclo. Incluso una respuesta inválida del LLM no puede impedir la evaluación de stops.

El debate de riesgo basado en LLM existente sigue situado antes del Portfolio Manager. `HardRiskEngine` es una segunda barrera local, cuantitativa y determinista.

## Acciones y ciclo de vida de posiciones

| Rating del Portfolio Manager | Acción efectiva |
|---|---|
| `Buy` | `BUY`, o `INCREASE` si ya existe la posición |
| `Overweight` | `INCREASE` |
| `Hold` | `HOLD` |
| `Underweight` | `REDUCE` un 50% |
| `Sell` | `CLOSE` el 100% |

Reglas adicionales:

- `BUY` sobre una posición existente se convierte en `INCREASE`;
- `INCREASE`, `REDUCE` o `CLOSE` sin posición son rechazados;
- una reducción o cierre nunca puede vender más unidades de las disponibles;
- `INCREASE` recalcula el precio medio ponderado de entrada;
- el P&L realizado de una venta es `proceeds_after_fees - quantity * average_entry_price`;
- `BUY` e `INCREASE` solicitan inicialmente un 5% del equity, siempre limitados por efectivo, máximo por posición y exposición total.

## Valoración, equity y drawdown

Antes de procesar una nueva decisión, `mark_to_market()` obtiene una cotización para cada posición abierta y actualiza:

- `current_price`, `last_price_update` y `price_stale`;
- valor de mercado y P&L no realizado de cada posición;
- efectivo, exposición, equity total y P&L total;
- rentabilidad respecto al capital inicial;
- `high_water_mark`, drawdown actual y máximo drawdown observado.

Cada ciclo añade un snapshot a `data/equity_history.json`. No se añade otro registro si el estado económico no ha cambiado; el timestamp por sí solo no genera un duplicado.

Al alcanzar un drawdown del 10%, `BUY` e `INCREASE` quedan bloqueados con `MAX_DRAWDOWN`. `REDUCE` y `CLOSE` siguen permitidos para disminuir riesgo.

## Salidas automáticas

Al abrir una posición se fijan niveles deterministas respecto al precio real de ejecución:

```text
STOP_LOSS_PERCENT       3%
TAKE_PROFIT_PERCENT     6%
TRAILING_STOP_PERCENT   3%
TRAILING_STOP_ENABLED   false
```

El trailing stop está implementado pero desactivado por defecto. Cuando se habilita, la posición mantiene `highest_price_since_entry` y se cierra si retrocede el porcentaje configurado desde ese máximo. Una posición con precio obsoleto no dispara una salida automática.

## Reglas duras de riesgo

Valores iniciales:

```text
INITIAL_BALANCE               50.00 EUR
MAX_POSITION_PERCENT             10%
MAX_TOTAL_EXPOSURE_PERCENT       50%
MAX_DAILY_LOSS_PERCENT            3%
MAX_DRAWDOWN_PERCENT             10%
MAX_OPEN_POSITIONS                 5
MIN_ORDER_VALUE_EUR             1.00 EUR
ALLOW_LEVERAGE                 false
ALLOW_NEGATIVE_BALANCE         false
```

Las entradas inferiores al mínimo se rechazan. Las reducciones y cierres sí pueden ejecutar importes residuales inferiores al mínimo para que una posición pequeña nunca quede atrapada. Las compras incluyen la comisión estimada antes de comprobar el efectivo. El broker vuelve a validar efectivo y cantidad aunque reciba una aprobación manipulada.

## Precios y moneda

`MarketPriceService` reutiliza `load_ohlcv()` de TradingAgents. Usa el último cierre liquidado disponible para la fecha del análisis y conserva los controles existentes de caché, obsolescencia y look-ahead.

La cartera está denominada en EUR. Si Yahoo informa otra divisa, se obtiene el cruce `<DIVISA>EUR=X` con la misma infraestructura. Las cotizaciones londinenses en peniques (`GBp`) se convierten primero a GBP. No es un feed tick-by-tick; son cierres de mercado. Si no hay precio o FX fiable, no se aumenta riesgo.

## Simulación del broker

```text
TRADING_FEE_PERCENT  0.001   (0.10%)
SLIPPAGE_PERCENT     0.0005  (0.05%)
SPREAD_PERCENT       0.0005  (0.05%)
```

`BUY`/`INCREASE` se ejecutan por encima y `REDUCE`/`CLOSE` por debajo del precio de referencia. Cada fill registra símbolo, acción, motivo, cantidad, precios solicitado y ejecutado, comisión, impacto adverso, valor, P&L realizado, timestamp y saldos anterior/posterior.

## Persistencia

Rutas predeterminadas:

```text
data/paper_portfolio.json
data/equity_history.json
```

La cartera contiene balances, métricas de equity/drawdown, posiciones y `trade_history`. Cada posición persiste cantidad, coste medio, precio actual, timestamps de precio/apertura, estado stale, P&L y niveles de salida. Ambos ficheros se escriben atómicamente mediante un temporal y están ignorados por Git.

Variables opcionales:

```dotenv
TRADINGAGENTS_PAPER_TRADING_ENABLED=true
TRADINGAGENTS_PAPER_PORTFOLIO_PATH=data/paper_portfolio.json
TRADINGAGENTS_PAPER_EQUITY_HISTORY_PATH=data/equity_history.json
```

Desactivar la capa conserva el análisis y devuelve `PAPER_TRADING_DISABLED` sin tocar la cartera.

## Inspección en CLI e informes

Tras cada análisis, la CLI muestra dos bloques:

- `PAPER PORTFOLIO`: efectivo, posiciones, equity, P&L realizado/no realizado/total, retorno y drawdowns;
- `LAST EXECUTION`: última acción, estado, motivo, precio, cantidad, comisión y P&L realizado.

El mismo contenido queda disponible como `paper_trading_result`, `paper_trading_report`, en `6_paper_trading/execution.md` y en el log JSON del run.

## Reset de la simulación

Con TradingAgents detenido, eliminar **ambos** ficheros de estado:

```text
data/paper_portfolio.json
data/equity_history.json
```

En el siguiente análisis se recrea una cartera de 50 EUR. Si se configuraron rutas personalizadas, deben resetearse esas rutas en su lugar.

## Archivos principales

| Responsabilidad | Archivo |
|---|---|
| Contratos y acciones | `tradingagents/paper_trading/models.py` |
| Parámetros | `tradingagents/paper_trading/config.py` |
| Cartera, P&L, drawdown y JSON | `tradingagents/paper_trading/portfolio.py` |
| Reglas duras | `tradingagents/paper_trading/risk_engine.py` |
| Fills y costes | `tradingagents/paper_trading/broker.py` |
| Precio y FX a EUR | `tradingagents/paper_trading/price_service.py` |
| Ciclo LangGraph e informe | `tradingagents/paper_trading/node.py` |

## Limitaciones actuales

- no existen órdenes reales ni conexiones con brokers/exchanges;
- no hay órdenes pendientes o límite, ejecución intradía ni libro de órdenes;
- no se modelan dividendos, intereses, splits, impuestos o sesiones parciales;
- los stops se evalúan con el precio disponible al ejecutar el pipeline, no continuamente;
- la persistencia JSON está diseñada para un único proceso y no tiene locking multiproceso;
- la precisión del fill sigue siendo una simulación fija de fee, spread y slippage.
