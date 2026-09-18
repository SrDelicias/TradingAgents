# Análisis del proyecto TradingAgents

Fecha del análisis: 2026-09-17  
Repositorio: `SrDelicias/TradingAgents`  
Commit analizado: `be952b8eccb49720509af544c6675233bc1f10d0` (`main`, v0.4.2)

> **Actualización posterior:** este documento conserva el diagnóstico del código
> upstream antes de modificarlo. La primera capa local de paper trading ya se ha
> implementado después de ese análisis. Su arquitectura y comportamiento actual
> están documentados en `docs/PAPER_TRADING.md`.

## Resumen ejecutivo

El proyecto es un **pipeline de análisis y recomendación multiagente**, orquestado con LangGraph. Recopila datos de mercado, genera cuatro informes especializados, enfrenta tesis alcistas y bajistas, crea una propuesta de Trader, la somete a un debate de riesgo y hace que un Portfolio Manager emita la recomendación final.

En su estado actual **no es un sistema de trading ni de paper trading**:

- no mantiene efectivo, posiciones, coste medio, P&L ni valor de cartera;
- no crea órdenes, fills, comisiones o slippage;
- no contiene un broker ni un exchange simulado;
- no ejecuta compras o ventas;
- no contiene un motor de backtesting de cartera, aunque `backtrader` figura como dependencia;
- sí puede analizar fechas históricas evitando parte del look-ahead y sí evalúa a posteriori una decisión mediante el retorno del activo a 5 sesiones.

Por tanto, la salida actual es una **recomendación** (`Buy / Overweight / Hold / Underweight / Sell`), no una operación ejecutada.

## Mapa de arquitectura

```text
CLI / API
  cli/main.py
  TradingAgentsGraph.propagate()
          |
          v
Instrument identity + memoria previa + estado LangGraph
          |
          v
Analyst Agents (secuenciales; cada agente puede iterar con sus tools)
  Market -> Sentiment -> News -> Fundamentals
          |
          v
Bull Researcher <-> Bear Researcher
          |
          v
Research Manager (plan de inversión de 5 niveles)
          |
          v
Trader (propuesta BUY / HOLD / SELL + niveles y sizing textual)
          |
          v
Aggressive -> Conservative -> Neutral Risk Analysts (debate cíclico)
          |
          v
Portfolio Manager (decisión final de 5 niveles)
          |
          v
END -> SignalProcessor -> señal devuelta al llamador
          |
          +-> logs JSON / informes Markdown
          +-> decision log y reflexión diferida
```

El grafo se construye en `tradingagents/graph/setup.py`. El estado compartido está tipado en `tradingagents/agents/utils/agent_states.py`; los bucles y límites de debate se deciden en `tradingagents/graph/conditional_logic.py`.

## Agentes existentes

### Analyst Agents

| Agente | Archivo | Función y fuentes principales |
|---|---|---|
| Market Analyst | `tradingagents/agents/analysts/market_analyst.py` | Obtiene OHLCV, hasta 8 indicadores técnicos y un snapshot verificado. Produce `market_report`. |
| Sentiment Analyst | `tradingagents/agents/analysts/sentiment_analyst.py` | Prefetch de noticias, StockTwits y Reddit; produce una banda, score, confianza y narrativa estructurados en `sentiment_report`. La clave histórica del grafo sigue siendo `social`. |
| News Analyst | `tradingagents/agents/analysts/news_analyst.py` | Noticias del activo y globales, FRED, mercados predictivos y contexto macro. Produce `news_report`. |
| Fundamentals Analyst | `tradingagents/agents/analysts/fundamentals_analyst.py` | Perfil fundamental, balance, cash flow e income statement. Produce `fundamentals_report`. |

`social_media_analyst.py` es solo un shim de compatibilidad que reexporta el nuevo Sentiment Analyst.

Los analistas seleccionados se ejecutan **en el orden recibido**, no en paralelo. El orden por defecto es Market, Sentiment, News y Fundamentals. En los analistas con tool calling, el flujo alterna `Agent -> ToolNode -> Agent` hasta que ya no hay llamadas a herramientas; después un nodo limpia los mensajes temporales y pasa al siguiente analista.

### Bull/Bear Researchers y Research Manager

| Agente | Archivo | Función |
|---|---|---|
| Bull Researcher | `tradingagents/agents/researchers/bull_researcher.py` | Construye y defiende la tesis alcista usando los cuatro informes y rebate al Bear. |
| Bear Researcher | `tradingagents/agents/researchers/bear_researcher.py` | Construye la tesis bajista, enfatiza riesgos y rebate al Bull. |
| Research Manager | `tradingagents/agents/managers/research_manager.py` | Juzga el debate y produce un `ResearchPlan` estructurado con rating de cinco niveles, rationale y acciones. |

Bull y Bear escriben sobre `investment_debate_state`. `ConditionalLogic.should_continue_debate()` alterna ambos hasta `2 * max_debate_rounds`; después enruta al Research Manager.

### Trader Agent

Archivo: `tradingagents/agents/trader/trader.py`.

Convierte el plan del Research Manager en un `TraderProposal`:

- `action`: `Buy`, `Hold` o `Sell`;
- razonamiento;
- entry price opcional;
- stop loss opcional;
- position sizing textual opcional.

Lee también `market_report` para apoyar niveles en precio, ATR y soportes/resistencias. Su resultado renderizado se guarda en `trader_investment_plan`. **No envía una orden**.

### Risk Management Agents

| Agente | Archivo | Función |
|---|---|---|
| Aggressive Analyst | `tradingagents/agents/risk_mgmt/aggressive_debator.py` | Favorece oportunidades de alta rentabilidad y tolera mayor riesgo. |
| Conservative Analyst | `tradingagents/agents/risk_mgmt/conservative_debator.py` | Prioriza preservación de capital, baja volatilidad y mitigación de pérdidas. |
| Neutral Analyst | `tradingagents/agents/risk_mgmt/neutral_debator.py` | Busca una postura equilibrada entre upside y protección. |

Los tres leen la propuesta del Trader y los informes. Se comunican mediante `risk_debate_state`; el router los alterna Aggressive -> Conservative -> Neutral hasta `3 * max_risk_discuss_rounds`. Son **agentes LLM consultivos**, no un Hard Risk Engine determinista: no pueden garantizar límites cuantitativos de exposición, cash, pérdida diaria o concentración.

### Portfolio Manager

Archivo: `tradingagents/agents/managers/portfolio_manager.py`.

Sintetiza:

- plan del Research Manager;
- propuesta del Trader;
- historial del debate de riesgo;
- lecciones de decisiones anteriores.

Produce un `PortfolioDecision` estructurado definido en `tradingagents/agents/schemas.py`, con rating `Buy / Overweight / Hold / Underweight / Sell`, resumen, tesis, target y horizonte opcionales. El objeto se renderiza inmediatamente a Markdown y se guarda en `final_trade_decision`.

Aunque el README afirma que el Portfolio Manager aprueba/rechaza y envía al simulated exchange, **ese envío no existe en el código**.

## Cómo se comunican los agentes

La comunicación no se realiza mediante colas o procesos separados. Todos son funciones/nodos dentro de un único `StateGraph(AgentState)`:

1. Cada nodo recibe el estado compartido completo.
2. Devuelve solo las claves que actualiza.
3. LangGraph combina esas actualizaciones en el estado del run.
4. Los analistas basados en tools usan también `messages` de LangChain y `ToolNode`.
5. Los debates usan subestados con `history`, respuesta actual, último speaker y contador.
6. Los managers y el Trader consumen campos de texto producidos por etapas anteriores.

Campos principales del hand-off:

```text
market_report / sentiment_report / news_report / fundamentals_report
    -> investment_debate_state
    -> investment_plan
    -> trader_investment_plan
    -> risk_debate_state
    -> final_trade_decision
```

Hay checkpointing opcional por ticker en SQLite (`tradingagents/graph/checkpointer.py`). Sirve para reanudar el grafo tras una interrupción; no es memoria financiera ni cartera.

## Dónde se genera BUY / SELL / HOLD

Hay tres niveles distintos:

1. **Research Manager**: genera una recomendación de cinco niveles en `ResearchPlan.recommendation`.
2. **Trader**: genera una propuesta de tres niveles en `TraderProposal.action` (`Buy / Hold / Sell`) y la guarda como Markdown en `trader_investment_plan`.
3. **Portfolio Manager**: genera la decisión final autoritativa de cinco niveles en `PortfolioDecision.rating`; `render_pm_decision()` la inserta como `**Rating**: X` en `final_trade_decision`.

Al terminar el grafo, `TradingAgentsGraph._run_graph()` pasa `final_trade_decision` a `SignalProcessor.process_signal()`. Este parser determinista devuelve `Buy / Overweight / Hold / Underweight / Sell`, o `REVIEW` si el texto no es reconocible. Esta es la señal retornada por `propagate()`.

La decisión final se origina, por tanto, en:

- `tradingagents/agents/managers/portfolio_manager.py` (`portfolio_manager_node`);
- schema y render: `tradingagents/agents/schemas.py` (`PortfolioDecision`, `render_pm_decision`);
- extracción pública: `tradingagents/graph/signal_processing.py`;
- devolución al llamador: `tradingagents/graph/trading_graph.py` (`_run_graph`).

## Punto limpio para integrar Hard Risk Engine y paper trading

El punto más limpio es **un nuevo nodo determinista inmediatamente después de `Portfolio Manager` y antes de cualquier broker**. Hoy el grafo termina con:

```python
workflow.add_edge("Portfolio Manager", END)
```

La evolución recomendada es:

```text
Trader
  -> debate de riesgo LLM existente
  -> Portfolio Manager
  -> Hard Risk Engine (determinista, sin LLM)
  -> Paper Broker
  -> Portfolio virtual
  -> END
```

En términos de LangGraph:

```text
Portfolio Manager -> Hard Risk Engine -> Paper Broker -> END
```

Este punto conserva la arquitectura actual: los agentes de riesgo siguen aportando análisis cualitativo, el Portfolio Manager emite la intención final y el Hard Risk Engine puede **rechazar, reducir o normalizar** esa intención según reglas inviolables y el estado real de la cartera.

No conviene interceptar únicamente el string devuelto después de `propagate()`, porque se perderían trazabilidad, checkpointing, auditoría y la posibilidad de distinguir propuesta, aprobación y fill dentro del estado. Antes de conectar el broker se debería conservar la salida estructurada del PM como datos (no solo Markdown) y añadir tipos explícitos, por ejemplo:

```text
portfolio_decision     # intención del PM
risk_decision          # APPROVE / RESIZE / REJECT + motivos y límites
order_intent           # orden normalizada, todavía sin ejecutar
execution_result       # fill/rechazo del Paper Broker
portfolio_snapshot     # cash, posiciones, NAV, P&L
```

Para una cartera inicial de 50 €, el Hard Risk Engine necesitará al menos precio ejecutable, cash disponible, posiciones actuales, tamaño mínimo/fraccional permitido, exposición máxima, comisiones/slippage y reglas para `Overweight`/`Underweight`. El Portfolio Manager actual no recibe un portfolio snapshot real; solo produce texto de sizing.

## Datos de mercado y precios actuales

El enrutador central está en `tradingagents/dataflows/interface.py`. Las tools del agente llaman `route_to_vendor()`, que usa `data_vendors` y `tool_vendors` de `tradingagents/default_config.py`.

Configuración por defecto:

| Categoría | Vendor por defecto | Alternativas |
|---|---|---|
| OHLCV/precios | Yahoo Finance (`yfinance`) | Alpha Vantage |
| Indicadores | Yahoo Finance + `stockstats` | Alpha Vantage |
| Fundamentales | Yahoo Finance | Alpha Vantage |
| Noticias | Yahoo Finance | Alpha Vantage |
| Macro | FRED | — |
| Mercados predictivos | Polymarket | — |
| Sentimiento adicional | StockTwits y Reddit | fetchers directos del Sentiment Analyst |

Detalles relevantes:

- `tradingagents/dataflows/y_finance.py`: OHLCV, fundamentales y estados financieros.
- `tradingagents/dataflows/stockstats_utils.py`: descarga/caché de cinco años, indicadores, filtro `Date <= trade_date`, control de datos obsoletos y TTL intradía.
- `tradingagents/dataflows/market_data_validator.py`: snapshot verificado usado como fuente de verdad para cifras exactas.
- `tradingagents/dataflows/symbol_utils.py`: normaliza aliases de brokers/forex/cripto a símbolos Yahoo.
- `TradingAgentsGraph._fetch_returns()`: consulta Yahoo directamente para evaluar decisiones pasadas contra el benchmark.

Yahoo Finance proporciona datos reales de mercado, pero el pipeline usa principalmente barras diarias y no constituye un feed transaccional en tiempo real. La caché del día actual puede contener una vela parcial hasta su refresco; el código usa un TTL de 15 minutos y evita presentar una barra sin cierre como cierre definitivo.

## Operaciones simuladas y backtesting

### Simulated exchange

**No existe una implementación.** No hay clases o módulos para órdenes, matching, fills, cash, posiciones, comisiones, slippage o broker. La palabra “simulated exchange” solo aparece como promesa conceptual en el README.

### Ejecución de operaciones simuladas

**No se ejecutan operaciones simuladas.** El flujo termina en una decisión textual/señal. Los logs y reportes no equivalen a fills ni actualizan una cartera.

### Backtesting

**No existe un motor de backtesting de cartera.** `backtrader>=1.9.78.123` está declarado en `pyproject.toml`, pero no hay ningún `import backtrader`, `Cerebro`, `Strategy` o broker de Backtrader en el repositorio.

Lo que sí existe es soporte parcial para análisis histórico correcto:

- `trade_date` recorre el grafo;
- OHLCV, noticias, social y fundamentales aplican filtros para reducir look-ahead;
- la memoria filtra lecciones por `resolution_date` en runs históricos;
- `_fetch_returns()` calcula retorno bruto y alpha tras 5 sesiones para juzgar una recomendación pasada.

Esto es una evaluación diferida de señales, no un backtest con capital, posiciones y ejecución.

## Memoria de decisiones

Hay dos mecanismos distintos:

1. **Decision log persistente** — `tradingagents/agents/utils/memory.py`:
   - ruta por defecto: `~/.tradingagents/memory/trading_memory.md`;
   - al final de un run guarda `final_trade_decision` como entrada `pending`;
   - al iniciar otro run del mismo ticker, intenta obtener el retorno a 5 sesiones y alpha contra un benchmark regional;
   - el `Reflector` genera una lección breve con un LLM;
   - decisiones y reflexiones previas se inyectan en el prompt del Portfolio Manager;
   - en análisis históricos, solo se incluyen resultados que ya eran conocidos en esa fecha.
2. **Checkpoints de ejecución** — SQLite bajo `~/.tradingagents/cache/checkpoints/` cuando se activa `checkpoint_enabled`; solo permiten reanudar nodos.

Además, `_log_state()` escribe el estado final completo en JSON y `reporting.py` puede generar el árbol de informes Markdown. Ninguno de estos mecanismos mantiene una cartera.

## Dependencias y configuración de ejecución

### Requisitos

- Python `>=3.10` (CI prueba 3.10, 3.11, 3.12 y 3.13).
- Instalación: `pip install .` o `pip install -e ".[dev]"` para desarrollo/tests.
- Entrada CLI: `tradingagents` o `python -m cli.main`.
- Alternativa Docker: `docker compose run --rm tradingagents`.

Dependencias principales: LangGraph/LangChain y clientes LLM, Pydantic, pandas, yfinance, stockstats, requests, Rich/Typer, SQLite checkpointer, Redis y Backtrader. En el código revisado, Redis y Backtrader no forman parte del flujo principal de ejecución.

### Variables y credenciales

- Es obligatorio configurar el proveedor LLM elegido: `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, etc.; Ollama/OpenAI-compatible puede ser local y no requerir key.
- `ALPHA_VANTAGE_API_KEY` solo si se selecciona Alpha Vantage.
- `FRED_API_KEY` habilita macro FRED; si falta, esta fuente opcional degrada de forma controlada.
- Polymarket, Yahoo Finance, Reddit y StockTwits no requieren credenciales en esta implementación.
- `.env` y `.env.enterprise` se cargan desde `tradingagents/__init__.py`.
- Los `TRADINGAGENTS_*` permiten cambiar proveedor/modelos, URL, rondas, idioma, temperatura, retries, tokens, checkpoint y benchmark.

Directorios por defecto:

```text
~/.tradingagents/cache/     datos y checkpoints
~/.tradingagents/logs/      resultados y estados JSON
~/.tradingagents/memory/    decision log
```

## Verificación realizada

Entorno usado: Windows, Python 3.12.14, entorno virtual local `.venv`.

```text
python -m pytest
676 passed, 2 skipped, 18 warnings in 33.40s
```

Omisiones:

- test opcional de Bedrock: no está instalado el extra `langchain_aws`;
- test live de DeepSeek: no se configuró `DEEPSEEK_API_KEY`.

Las 18 advertencias corresponden principalmente a modelos deliberadamente desconocidos utilizados por tests de validación; no son fallos.

También se ejecutó:

```text
python -m ruff check .
All checks passed!
```

Conclusión: la suite unitaria y el lint están sanos. No se ejecutó un análisis LLM end-to-end live porque requeriría credenciales, consumiría una API externa y no forma parte de los tests automatizados. El proyecto funciona correctamente como framework de análisis y generación de señales; todavía no implementa ejecución ni contabilidad de paper trading.

## Archivos clave

| Área | Archivo |
|---|---|
| Orquestador principal | `tradingagents/graph/trading_graph.py` |
| Definición y edges del grafo | `tradingagents/graph/setup.py` |
| Estado compartido | `tradingagents/agents/utils/agent_states.py` |
| Routers de debates/tools | `tradingagents/graph/conditional_logic.py` |
| Schemas estructurados | `tradingagents/agents/schemas.py` |
| Señal final | `tradingagents/graph/signal_processing.py` |
| Configuración | `tradingagents/default_config.py` |
| Routing de datos | `tradingagents/dataflows/interface.py` |
| Yahoo/indicadores/caché | `tradingagents/dataflows/y_finance.py`, `stockstats_utils.py` |
| Memoria | `tradingagents/agents/utils/memory.py`, `tradingagents/graph/reflection.py` |
| Checkpointing | `tradingagents/graph/checkpointer.py` |
| CLI | `cli/main.py`, `cli/config.py`, `cli/utils.py` |
| Informes | `tradingagents/reporting.py` |
| Tests | `tests/` |

## Conclusión para la siguiente fase

El contrato correcto de integración es separar con claridad:

```text
LLM recommendation -> deterministic authorization -> simulated execution -> portfolio accounting
```

Aplicado a este repositorio:

```text
Trader
  -> Risk debate LLM
  -> Portfolio Manager
  -> Hard Risk Engine
  -> Paper Broker
  -> Virtual Portfolio (50 € iniciales)
```

El cambio mínimo y más limpio comienza sustituyendo el edge `Portfolio Manager -> END` por un nodo Hard Risk Engine. El Paper Broker y la cartera deben consumir únicamente una orden autorizada por ese motor, nunca texto LLM directamente y nunca credenciales o conectores capaces de operar dinero real.
