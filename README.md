# Gen 3 Pokémon Team Optimizer

A MILP-based team optimizer for Pokémon FireRed/LeafGreen that finds the strongest 6-Pokémon team with full super-effective type coverage.

## How It Works

The optimizer uses a single-phase regularised max-min mixed-integer linear program:

**Objective**: Maximise `z + ε · total_power`, where `z` is a lower bound on coverage across all 17 defending types. This finds the team with the best worst-case type coverage, breaking ties in favour of higher total firepower.

**Constraints**:
- **Type overlap cap** - no more than `n` Pokémon sharing any single type (default 2)
- **SE redundancy** - at least `k` Pokémon with a super-effective move against every defending type (default 2)

Each move is scored by: `power × accuracy × STAB × type effectiveness × stat × speed × recoil × priority`, using Gen 3-accurate mechanics (physical/special determined by type, not move). Self-damaging moves (Double-Edge, Take-Down, Submission) are penalised proportionally to their recoil, and unreliable negative-priority moves (Focus Punch) are heavily discounted.

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

Then open [http://localhost:5001](http://localhost:5001). The web interface exposes all solver parameters and team constraints through an interactive dashboard.

## CLI Usage

```bash
python -m optimiser                                          # default run (no legendaries)
python -m optimiser --allow-legendaries                      # include legendaries
python -m optimiser --lock charizard                         # force Charizard on team
python -m optimiser --lock-move charizard flamethrower       # force a move on a locked Pokémon
python -m optimiser --must-have earthquake                   # require Earthquake on someone
python -m optimiser --max-overlap 3                          # allow 3 Pokémon of same type
python -m optimiser --min-redundancy 1                       # relax SE redundancy to 1
python -m optimiser --acc-exponent 3.0                       # harsher penalty on low-accuracy moves
python -m optimiser --duplicate-type-discount 0.0            # 2nd same-type move gets no credit
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
    └── solver.py                          # Regularised max-min MILP (PuLP)
```

## Tunable Parameters

| Param                        | Default | Description                                                                               |
| ---------------------------- | ------- | ----------------------------------------------------------------------------------------- |
| `max_overlap`                | 2       | Max Pokémon on the team sharing any single type                                            |
| `min_redundancy`             | 2       | Min Pokémon with a super-effective move required against every defending type               |
| `acc_exponent`               | 2.0     | Accuracy penalty exponent: `(acc/100)^exp`. Higher values strongly favor accurate moves.   |
| `duplicate_type_discount`    | 0.5     | Credit for a 2nd same-type move on one Pokémon (0 = no credit, 1 = full value)             |
| `low_priority_factor`        | 0.3     | Multiplier for negative-priority moves like Focus Punch (0 = ignore, 1 = full value)       |

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