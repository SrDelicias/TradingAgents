# Dashboard local de paper trading

## Alcance

El dashboard es una capa local de observación construida con Streamlit y Plotly. Lee el estado producido por TradingAgents y no ejecuta análisis, no llama a LLMs, no solicita precios y no contiene conectividad con brokers o exchanges.

La interfaz está optimizada para un monitor de escritorio de 1920×1080 y se adapta a 1366×768. Utiliza tema oscuro, formato numérico europeo y actualización automática cada dos segundos.

## Instalación

Desde la raíz del repositorio:

```powershell
python -m pip install -e .
```

Las dependencias de interfaz declaradas en `pyproject.toml` son:

- `streamlit>=1.37.0`;
- `plotly>=5.24.0`.

## Ejecución

Estado real de la cartera local:

```powershell
streamlit run dashboard/app.py
```

La aplicación queda disponible en:

```text
http://localhost:8501
```

Modo demostración, sin leer ni escribir la cartera real:

```powershell
streamlit run dashboard/app.py -- --demo
```

## Fuentes de datos

El dashboard realiza únicamente lecturas locales:

| Fuente | Contenido |
|---|---|
| `data/paper_portfolio.json` | efectivo, posiciones, métricas y operaciones |
| `data/equity_history.json` | evolución histórica del equity |
| `data/events.jsonl` | actividad estructurada del pipeline y la ejecución |

Las rutas pueden cambiarse mediante:

```dotenv
TRADINGAGENTS_PAPER_PORTFOLIO_PATH=data/paper_portfolio.json
TRADINGAGENTS_PAPER_EQUITY_HISTORY_PATH=data/equity_history.json
TRADINGAGENTS_PAPER_EVENTS_PATH=data/events.jsonl
```

Los lectores toleran ficheros inexistentes, JSON incompleto y líneas de eventos dañadas. En esos casos presentan estados vacíos en lugar de romper la interfaz.

## Sistema de eventos

`tradingagents.monitoring.EventLog` escribe un registro JSON por línea:

```json
{
  "timestamp": "2026-09-17T16:42:20+00:00",
  "type": "RISK_APPROVED",
  "stage": "Hard Risk Engine",
  "symbol": "BTC-USD",
  "status": "COMPLETED",
  "message": "Hard Risk Engine → APPROVED",
  "metadata": {
    "reason": "APPROVED"
  }
}
```

Se publican eventos de inicio y finalización del análisis, ejecución de agentes, errores, precios, decisiones, normalización, aprobación/rechazo de riesgo, órdenes, cambios de posición, stops y actualización de cartera. Los eventos contienen resúmenes; no guardan los textos extensos de los LLM.

La escritura es best-effort: un fallo del fichero de monitorización no interrumpe el pipeline financiero.

## Auto-refresh

La vista principal se ejecuta dentro de un fragmento Streamlit con refresco cada dos segundos. Cada refresco vuelve a leer únicamente los tres ficheros locales. No inicia análisis ni realiza tráfico de mercado.

## Secciones

- **Portfolio:** KPI, gráfico de equity, análisis actual, decisión trazable, posiciones, razonamiento resumido, pipeline, actividad y operaciones recientes.
- **Agents:** pipeline visual, decisión actual y terminal de actividad.
- **Trades:** últimas 20 operaciones con costes, P&L y motivo.
- **Performance:** estadísticas de operaciones de salida, P&L, fees y drawdowns.
- **Risk:** límites efectivos del `HardRiskEngine` y niveles de posiciones.
- **System:** estado actual, actividad y rutas consumidas.

El filtro de símbolo afecta a posiciones, actividad y operaciones. Los rangos del gráfico son `1H`, `6H`, `24H`, `7D` y `ALL`. `AUTO SCALE` ajusta el eje Y alrededor del equity y conserva la referencia del capital inicial; `FULL SCALE` vuelve a mostrar el rango desde cero.

Los eventos de Bull Researcher, Bear Researcher y Portfolio Manager guardan únicamente un extracto saneado de hasta 260 caracteres para las tarjetas de razonamiento. El dashboard nunca expone la respuesta completa de los LLM.

## Reset local

El sidebar contiene un reset únicamente para la simulación. Requiere dos confirmaciones explícitas: marcar la casilla de advertencia y escribir `RESET`. Elimina exclusivamente los ficheros configurados de cartera y equity; no toca memoria, informes ni eventos. Está deshabilitado en modo demo.

## Estructura

```text
dashboard/
├── app.py
├── demo_data.py
├── components/
│   └── layout.py
├── styles/
│   └── theme.css
└── utils/
    ├── data.py
    ├── formatting.py
    └── stats.py

tradingagents/monitoring/
└── events.py
```

## Limitaciones

- actualización por polling local, no WebSocket;
- no hay scheduler integrado, por lo que “Next analysis” muestra `Not scheduled`;
- el menú, toolbar y footer de Streamlit se ocultan mediante la configuración oficial disponible y CSS; los selectores internos podrían requerir ajuste tras una actualización mayor de Streamlit;
- el dashboard debe ejecutarse en el mismo equipo o sobre el mismo filesystem;
- no incluye autenticación ni está preparado para exposición pública;
- las estadísticas consideran ganadoras/perdedoras las operaciones `REDUCE` y `CLOSE` con P&L realizado;
- no existen controles BUY/SELL ni trading manual.
