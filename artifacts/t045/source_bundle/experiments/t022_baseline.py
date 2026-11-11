"""T022 step 1: measure the current full-replay snapshot load (no behavior change)."""
import json, sys, tempfile
from pathlib import Path
from time import perf_counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime

out = {'runtime': runtime(), 'rows': []}
for cond in ('league_open_solidarity', 'league_open_solidarity' + le.POPULATION_SUFFIX):
    for days in (35, 84, 1000):
        t = perf_counter(); w = le.run_league_world(seed=100, days=days, config=le.config_for_condition(cond)); run_s = perf_counter() - t
        with tempfile.TemporaryDirectory() as d:
            t = perf_counter(); le.save_league_artifacts(w, d); save_s = perf_counter() - t
            size = (Path(d) / 'snapshot.json').stat().st_size
            loads = []
            for _ in range(3):
                t = perf_counter(); le.load_league_snapshot(Path(d) / 'snapshot.json'); loads.append(perf_counter() - t)
        row = dict(condition=cond, days=days, run_s=round(run_s, 3), save_s=round(save_s, 3), load_s=[round(x, 3) for x in loads], bytes=size, events=len(w.events))
        out['rows'].append(row); print(row, flush=True)
Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
