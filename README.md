# Gen 3 Pokémon Team Optimizer

A MILP-based team optimizer for Pokémon FireRed/LeafGreen that finds the strongest 6-Pokémon team with full super-effective type coverage.

The main goal is to find a team where each Pokémon is a strong multi-type specialist, and the team collectively covers all types with best-in-class attackers.

## How It Works

The optimizer uses a lexicographic max-min mixed-integer linear program:

**Objective**: Solve lexicographically: first maximise coverage `z`, then minimise duplicate attacking types (both within each Pokémon and across the full team), then maximise total firepower. This finds the best worst-case type coverage without relying on exposed weight tuning.

**Constraints**:

- **Type overlap cap** - no more than `n` Pokémon sharing any single type (default 1)
- **SE redundancy** - at least `k` Pokémon with a super-effective move against every defending type (default 2)

Each move is scored by: `power × accuracy × STAB × type effectiveness × stat × speed × recoil × priority`, using Gen 3-accurate mechanics (physical/special determined by type, not move). Self-damaging moves (Double-Edge, Take-Down, Submission) are penalised proportionally to their recoil, and unreliable negative-priority moves (Focus Punch) are heavily discounted.

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
| `min_redundancy`          | 2       | At least this many Pokémon must have a super-effective move against each enemy type.      |
| `max_same_type_moves`     | 2       | Max moves of the same attacking type per Pokémon. Lower values force broader movesets.    |
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

