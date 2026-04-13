# Pokemon Team Optimizer

Build the strongest 6-Pokemon team for an in-game playthrough. The optimizer picks Pokemon and movesets that give you the best type coverage and hardest-hitting attackers across all matchups, so nothing walls your team.

## What This Is For

- **In-game playthroughs** - story mode, gym leaders, rival fights
- **Full super-effective coverage** - at least one team member hits every type for super-effective damage
- **Strong individual Pokemon** - each team member pulls its weight with high-damage, accurate moves

This is **not** for competitive/PvP teambuilding or Elite Four min-maxing. It doesn't model defensive stats, held items, EVs/IVs, or switching. It finds the team that hits hardest across all 17 types with a single attacker per matchup, which is exactly what you want when steamrolling through a story.

## Supported Games

| Game | Generation | Dex Options |
| ---- | ---------- | ----------- |
| FireRed / LeafGreen | Gen 3 | Regional (Kanto 151) or National (386) |
| Brilliant Diamond / Shining Pearl | Gen 8 | Regional (Sinnoh) or National (493) |

Adding new games is straightforward - see `data/PROFILE_RESEARCH.md`.

## Quick Start

### Environment setup

```bash
pixi install
```

`pixi` is the primary environment manager for this repo.

### CLI

```bash
pixi run cli
```

### Web UI

```bash
pixi run web
```

Then open [http://localhost:5001](http://localhost:5001). The web interface exposes solver parameters and team constraints through a dashboard.

## CLI Usage

```bash
pixi run cli --help                                          # show all CLI options
pixi run cli                                                 # default run (no legendaries)
pixi run cli --version-group brilliant-diamond-shining-pearl-regional  # switch games
pixi run cli --allow-legendaries                             # include legendaries
pixi run cli --lock charizard                                # force Charizard on team
pixi run cli --lock-move charizard flamethrower              # force a move on a locked Pokemon
pixi run cli --must-have earthquake                          # require Earthquake on someone
pixi run cli --max-overlap 2                                 # allow 2 Pokemon sharing a type
pixi run cli --min-redundancy 1                              # relax SE redundancy to 1
pixi run cli --max-same-type-moves 1                         # force 4 distinct attacking types per Pokemon
pixi run cli --min-role-types 1                              # require each mon to own at least 1 matchup role
pixi run cli --role-threshold-pct 90                         # only count roles within 90% of best
pixi run cli --acc-exponent 3.0                              # harsher penalty on low-accuracy moves
pixi run cli --must-have-type water                          # require at least one Water-type Pokemon
pixi run cli --data path/to/custom.json                      # use a custom compiled JSON file
```

## How It Works

The optimizer uses a lexicographic max-min mixed-integer linear program (MILP) to find the best team. In plain terms, it solves three priorities in order:

1. **Maximize worst-case coverage** - make the weakest matchup as strong as possible
2. **Minimize duplicate attacking types** - spread move types across the team
3. **Maximize total firepower** - among tied teams, prefer more raw damage

Each move is scored by combining power, accuracy, STAB, type effectiveness, the relevant attacking stat, speed, and penalties for recoil/self-KO/drawback moves. The solver then picks 6 Pokemon and 4 moves each to maximize coverage.

See `OPTIMISATION.md` for the full mathematical formulation.

## Tunable Parameters

| Param | Default | What it does |
| ----- | ------- | ------------ |
| `max_overlap` | 3 | How many team members can share a type (e.g. 2 = at most 2 Water-types) |
| `min_redundancy` | 1 | Minimum number of Pokemon with a super-effective move against each type |
| `max_same_type_moves` | 2 | Max moves of the same type per Pokemon (1 = all different types) |
| `min_role_types` | 1 | Each Pokemon must be the best attacker against at least this many types |
| `role_threshold_pct` | 80 | A role only counts if the move scores within this % of the global best |
| `acc_exponent` | 2.0 | Accuracy penalty harshness (higher = low-accuracy moves punished more) |
| `speed_bonus` | 0.25 | Bonus for fast Pokemon (0.25 = fastest gets 1.25x, slowest gets 1.0x) |

## User Constraints

- **Lock a Pokemon** onto the team (`--lock`)
- **Lock specific moves** on a locked Pokemon (`--lock-move`)
- **Require a move** (e.g. Earthquake) without specifying who learns it (`--must-have`)
- **Require a Pokemon type** (e.g. Water) on the team (`--must-have-type`)
- **Ban legendaries** (default) or allow them (`--allow-legendaries`)

## Data Pipeline

```bash
pixi run compile-frlg    # FireRed / LeafGreen
pixi run compile-bdsp    # Brilliant Diamond / Shining Pearl
```

Fetches from [PokeAPI](https://pokeapi.co/) via `pokebase` with local caching. The compiled JSON includes dataset metadata, legendary/fully-evolved flags, move categories, recoil data, and priority flags.

## Project Structure

```
├── app.py                        # Flask web server & API
├── templates/
│   └── index.html                # Web UI (single-page app)
├── data/
│   ├── pokedex.py                # Data compiler (PokeAPI → JSON)
│   ├── profiles.py               # Game/generation profile definitions
│   └── compiled/                 # Pre-built datasets per game
├── optimiser/
│   ├── main.py                   # CLI entry point, data loading, output
│   ├── scoring.py                # Type chart & move score computation
│   └── solver.py                 # Lexicographic max-min MILP (PuLP)
├── tests/                        # Test suite
├── pixi.toml                     # Environment & task definitions
└── OPTIMISATION.md               # Full MILP formulation
```

## Tech Stack

- Python
- [PuLP](https://coin-or.github.io/pulp/) (MILP modelling)
- [HiGHS](https://highs.dev/) (MILP solver)
- [Flask](https://flask.palletsprojects.com/) (web UI)
- [PokeAPI](https://pokeapi.co/) (data source)
- [Pokebase](https://github.com/PokeAPI/pokebase) (Python API client)

## Contributing

Happy to accept PRs for:

- New game/generation support (especially if PokeAPI has the data)
- Improvements to the optimisation model
- UI/UX improvements

See `data/PROFILE_RESEARCH.md` for how to add a new game.
