"""T023 step 1 (before any code change): digest of the default 14 worlds at 126 days, identity excluded."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soccer_sim import league_economy as le
from soccer_sim.storage import digest, runtime

rows = {}
for name in [c + s for c in le.T011_ALL_CONDITIONS for s in ('', le.POPULATION_SUFFIX)]:
    raw = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=126, config=le.config_for_condition('league_' + name)))
    raw.pop('identity')
    rows[name] = digest(raw)
Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)
Path(sys.argv[1]).write_text(json.dumps({'runtime': runtime(), 'seed': 100, 'days': 126, 'digests_excluding_identity': rows}, indent=1))
print(len(rows))
