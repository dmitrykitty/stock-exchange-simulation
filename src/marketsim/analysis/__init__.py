"""Analysis of the F7 experiments from run logs: per-run metrics, paired statistics and the answer tables.

The notebooks ``07_e5_propagation`` and ``08_e7_network_share`` first computed these tables; the same code lives here
so that the main study is rebuilt by one command (``python -m marketsim.analysis.study``) and the notebooks only
show the result.

- ``runs``: metrics of one run (propagation along the graph, comovement of unrelated companies, collapse rule) and
  the response to a company shock in a pair of runs;
- ``stats``: paired bootstrap contrasts by seed, difference in differences, Wilson intervals, Fisher's exact test;
- ``study``: the experiments E5, E6 and E7 of the main study, their tables and the command line.
"""
