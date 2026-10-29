# Роли D1 и D2: данные и граф

**D1 — ряды цен и проверка модели.** Фазы: [F1](../phases/F1_data_collection.md) → метрики (F4) → [F8](../phases/F8_validation.md) → [F9](../phases/F9_visualization.md).
**D2 — привязка компаний и граф зависимостей.** Фазы: [F1](../phases/F1_data_collection.md) → [F2](../phases/F2_dependency_graph.md) → помощь S в [F5](../phases/F5_graph_valuers.md)–[F6](../phases/F6_full_network.md).

**Вы владеете:** схемами и загрузчиками данных (`src/marketsim/data/schemas.py`, `api.py`) и контрактом K2 (граф, внешние сигналы).
**Вы пользуетесь:** K3 (лог прогона — для метрик и проверки).

Зачем нужна каждая функция и кто её вызывает: [src/marketsim/data/README.md](../../../src/marketsim/data/README.md), [src/marketsim/graph/README.md](../../../src/marketsim/graph/README.md).

## Главное правило

Остальные читают ваши данные **только** через `marketsim.data.api` и `marketsim.graph.load_graph`. Поэтому **имена функций, их параметры, имена файлов, колонки и типы не меняются** (это контракт). Всё остальное — свободно: как скачивать, как чистить, какие вспомогательные функции и модули добавить. Таблицы записываются через `api.write_table`, которая сама проверяет схему.

## До пятницы 30.10 (этап A)

| Когда | D1 | D2 |
|---|---|---|
| Ср, вечер | окружение; FRED (нефть, газ, S&P 500, медь) | окружение; BEA: разобрать, совпасть с контрольными числами |
| Чт, утро | Yahoo: 18 акций + 5 фьючерсов | (по желанию) EDGAR: сверить SIC-коды с `companies.csv`; привязка уже сделана |
| Чт, день | French, World Bank, сверки | регрессии весов цепочки (F2, таблица 3.1) |
| Чт, вечер | notebook `01_data_access_check` | черновой граф для 18 компаний |
| Пт, утро | общий отчёт в чат с чек-листом (F1 10.1, F2 раздел 8) | |

## Дальше

| Неделя | D1 | D2 |
|---|---|---|
| 2 | `fetch.py`, `clean.py` по контракту; `check --strict` зелёный | BEA и EDGAR в коде |
| 3 | метрики по логу K3 | `candidate_company_edges`, `estimate_weights` |
| 4 | анализ E1–E3 | `reference_path`, сценарии внешних сигналов |
| 5 | каркас проверки F8 на синтетике | граф `real_v1`; F5 с S |
| 6–7 | дашборд F9 | F6 с S; чувствительность к весам |
| 8–9 | проверка F8 полностью, анализ экспериментов | восстановление графа, раздел отчёта |

## Команды

```bash
pip install -e ".[dev,data]"
```

```bash
python -m marketsim.data.check
```

```bash
pytest tests/contracts/test_data_contract.py tests/contracts/test_graph_contract.py
```
