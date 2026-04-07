# Gen 3 Pokémon Team Optimizer

A MILP-based team optimizer for Pokémon FireRed/LeafGreen that finds the strongest 6-Pokémon team with full super-effective type coverage.

The main goal is to find a team where each Pokémon is a strong multi-type specialist, and the team collectively covers all types with best-in-class attackers.

## How It Works

The optimizer uses a lexicographic max-min mixed-integer linear program:

**Objective**: Solve lexicographically: first maximise coverage `z` across all non-immune single-type matchups, then minimise duplicate attacking types (both within each Pokémon and across the full team), then maximise total non-immune firepower. This finds the best worst-case type coverage without relying on exposed weight tuning.

**Constraints**:

- **Type overlap cap** - no more than `n` Pokémon sharing any single type (default 1)
- **SE redundancy** - at least `k` selected (Pokémon, move) pairs that are super-effective against every defending type (default 2)
- **Role-aware diversity** - designated attackers must use a qualifying super-effective move that scores within `p%` of the global best for that type, and each selected Pokémon must own at least `r` such roles when the quota is enabled (defaults: `r=2`, `p=80`)

Each move is scored by: `power × accuracy × STAB × type effectiveness × stat × speed × recoil × priority × move-specific discount`, using Gen 3-accurate mechanics (physical/special determined by type, not move). Before scoring, non-machine/non-tutor attacking moves are heuristically pruned per Pokémon within each attacking type: any move below 80% of that type's best effective-power score is removed, while TM/HM/tutor moves and user-locked moves are preserved. The score table stores only neutral-or-better (≥1×) matchups; not-very-effective and immune entries are skipped. Stages 1 and 3 optimise over that score set, while the redundancy constraint still targets super-effective coverage. Self-damaging moves (Double-Edge, Take-Down, Submission) are penalised proportionally to their recoil, `Explosion`/`Self-Destruct` are further discounted because they KO the user, `Frustration` is mildly discounted because it assumes deliberately minimized friendship, and unreliable negative-priority moves (Focus Punch) are heavily discounted.

See OPTIMISATION.md for full description on the MILP problem.

## Quick Start

### CLI

```bash
pip install -r requirements.txt
python -m optimiser
```

### Web UI

```bash
pip install -r requirements.txt
python app.py
```

Then open [http://localhost:5001](http://localhost:5001). The web interface exposes the user-facing solver parameters and team constraints through an interactive dashboard.

## CLI Usage

```bash
python -m optimiser                                          # default run (no legendaries)
python -m optimiser --allow-legendaries                      # include legendaries
python -m optimiser --lock charizard                         # force Charizard on team
python -m optimiser --lock-move charizard flamethrower       # force a move on a locked Pokémon
python -m optimiser --must-have earthquake                   # require Earthquake on someone
python -m optimiser --max-overlap 2                          # allow 2 Pokémon sharing a type
python -m optimiser --min-redundancy 1                       # relax SE redundancy to 1
python -m optimiser --max-same-type-moves 1                  # force 4 distinct attacking types per Pokémon
python -m optimiser --min-role-types 1                       # require each selected mon to own at least 1 matchup role
python -m optimiser --role-threshold-pct 90                  # only count roles that are within 90% of best
python -m optimiser --acc-exponent 3.0                       # harsher penalty on low-accuracy moves
python -m optimiser --must-have-type water                   # require at least one Water-type Pokémon
python -m optimiser --data path/to/custom.json               # use a custom compiled JSON file
```

## Project Structure

```
gen3-optim/
├── README.md
├── requirements.txt
├── app.py                                 # Flask web server & API
├── templates/
│   └── index.html                         # web UI (single-page app)
├── data/
│   ├── pokedex.py                         # data fetcher (PokeAPI → compiled JSON)
│   └── compiled/
│       └── firered-leafgreen.json         # 151 Pokémon with stats, types, and learnsets
└── optimiser/
    ├── __init__.py
    ├── __main__.py                        # python -m optimiser entry point
    ├── main.py                            # CLI, data loading, output display
    ├── scoring.py                         # Gen 3 type chart & score pre-computation
    └── solver.py                          # Lexicographic max-min MILP (PuLP)
```

## Tunable Parameters


| Param                     | Default | Description                                                                              |
| ------------------------- | ------- | ---------------------------------------------------------------------------------------- |
| `max_overlap`             | 1       | How many team members can share a type. E.g. 2 means at most 2 Water-types.              |
| `min_redundancy`          | 2       | At least this many selected (Pokémon, move) pairs must be super-effective against each enemy type. |
| `max_same_type_moves`     | 2       | Max moves of the same attacking type per Pokémon. Lower values force broader movesets.    |
| `min_role_types`          | 2       | Each selected Pokémon must be the designated role-holder for at least this many defending types. Set `0` to remove the per-Pokémon quota. |
| `role_threshold_pct`      | 80      | A designated attacker must use a super-effective move that scores at least this percent of the global best for that defending type. Set `0` to allow any positive-scoring SE move. |
| `acc_exponent`            | 2.0     | Accuracy penalty: mult = `(acc/100)^exp`. At 2.0, 85% acc → 0.72×, 70% acc → 0.49×.     |
| `speed_bonus`             | 0.25    | Bonus for fast Pokémon. At 0.25, the fastest gets 1.25× damage, the slowest gets 1.0×.   |


## User Constraints

- **Lock a Pokémon** onto the team (`--lock`)
- **Lock specific moves** on a locked Pokémon (`--lock-move`)
- **Require a move** (e.g., Earthquake) without specifying who learns it (`--must-have`)
- **Require a Pokémon type** (e.g., Water) on the team (`--must-have-type`)
- **Ban legendaries** (default) or allow them (`--allow-legendaries`)

## Data Pipeline

```bash
cd data
python pokedex.py compile firered-leafgreen
```

Fetches from [PokeAPI](https://pokeapi.co/) with local caching. The compiled JSON includes Gen 3-accurate typings (no Fairy), legendary/fully-evolved flags, multi-turn move tags, recoil percentages, and low-priority flags.

## Tech Stack

- Python
- [PuLP](https://coin-or.github.io/pulp/) (MILP modelling)
- [HiGHS](https://highs.dev/) (MILP solver)
- [Flask](https://flask.palletsprojects.com/) (web UI)
- [PokeAPI](https://pokeapi.co/) (data source)

## Contribute

I don't plan to upgrade this to support other gens unless..

- Nintendo release another Pokemon game on switch + pokeapi have the data
- There's a new cool idea for the optimisation problem (go to Discussion to mention it?)
- I get bored again

