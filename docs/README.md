# Documentation

The documents in this folder are written in Russian. In Polish, for the instructor: the research proposal, the progress overview (`progress_overview.md`) and the reports under `report/`.

## Where to start

0. [team_checklist.md](team_checklist.md): what everyone must read and do, where things are, the rules. **Go through it first.**
1. [model/explainer.md](model/explainer.md): the model in plain language, with worked numbers.
2. [plans/overall_plan.md](plans/overall_plan.md): phases, schedule, milestones, the goal for Friday.
3. Your role plan: [O, engine](plans/roles/engine_owner.md) · [D1, D2, data and graph](plans/roles/data_team.md) · [S, simulation](plans/roles/simulation_owner.md).
4. [../AGENTS.md](../AGENTS.md): repository rules.
5. [guides/f4_simulation.md](guides/f4_simulation.md): run the implemented single-asset simulation and reproduce E0–E3.
6. [guides/f5_graph_valuers.md](guides/f5_graph_valuers.md): the multi-asset graph market, its calibration and experiment E4.
7. [guides/f6_full_network.md](guides/f6_full_network.md): the full 18-company network on the real signals 2024–2025 and experiment E5.
8. [guides/f7_experiments.md](guides/f7_experiments.md): experiments E5–E7 and the main study.
9. [guides/f9_visualization.md](guides/f9_visualization.md): turn a recorded run into an HTML replay page, or run the market live and shock it from the page.
10. [progress_overview.md](progress_overview.md): what has been done, stage by stage, with the results (Polish; Russian version: [progress_overview_explained_ru.md](progress_overview_explained_ru.md)).

## All documents

| Document | Contents | Language |
|---|---|---|
| [research_proposal.md](research_proposal.md) | research proposal for the instructor, mini-paper format | Polish |
| [progress_overview.md](progress_overview.md) | progress overview for the instructor: stages, results, decisions | Polish |
| [progress_overview_explained_ru.md](progress_overview_explained_ru.md) | the progress overview explained for the team | Russian |
| [plans/main_study_protocol.md](plans/main_study_protocol.md) | frozen protocol of the main study: questions, metrics, decision rules | Russian |
| [model/model_description.md](model/model_description.md) | the model: graph, agents, data, questions, validation, experiments | Russian |
| [model/explainer.md](model/explainer.md) | the same in plain language: formulas, examples, FAQ, glossary | Russian |
| [model/contracts.md](model/contracts.md) | interfaces between parts and how to change them | Russian |
| [plans/overall_plan.md](plans/overall_plan.md) | overall plan | Russian |
| [plans/roles/](plans/roles/) | per-role plans | Russian |
| [plans/phases/](plans/phases/) | every phase F0–F10: goal, interfaces, steps, checks | Russian |
| [guides/f4_simulation.md](guides/f4_simulation.md) | F4 commands, agent parameters, logging conventions and experiment results | Russian |
| [guides/f5_graph_valuers.md](guides/f5_graph_valuers.md) | F5 graph valuers: rules, parameters, calibration, E4 results, speed | Russian |
| [guides/f6_full_network.md](guides/f6_full_network.md) | F6 full network: settings, calibration, E5, speed | Russian |
| [guides/f7_experiments.md](guides/f7_experiments.md) | F7 experiments E5–E7: commands, metrics, results | Russian |
| [guides/f9_visualization.md](guides/f9_visualization.md) | F9 replay page and live mode: commands, panels, control events, defence demo | Russian |

## Phases

| | Phase | Owner |
|---|---|---|
| F0 | [Specification and setup](plans/phases/F0_specification.md) | everyone |
| F1 | [Data collection](plans/phases/F1_data_collection.md) | D1, D2 |
| F2 | [Dependency graph](plans/phases/F2_dependency_graph.md) | D2 |
| F3 | [Exchange engine and gateway](plans/phases/F3_exchange_engine.md) | O |
| F4 | [Single-asset market](plans/phases/F4_single_asset_market.md) | S |
| F5 | [Graph valuers on a chain](plans/phases/F5_graph_valuers.md) | S + D2 |
| F6 | [Full network on real data](plans/phases/F6_full_network.md) | S + D2 + O |
| F7 | [Experiments](plans/phases/F7_experiments.md) | everyone |
| F8 | [Validation on real data](plans/phases/F8_validation.md) | D1 |
| F9 | [Visualization](plans/phases/F9_visualization.md) | D1 + O |
| F10 | [Report and defense](plans/phases/F10_report_and_defense.md) | everyone |
