"""
CLI entry point, data loading, orchestration, and output display.
"""

import argparse
import json
from pathlib import Path

from data.pokedex import FRLG_UNLIMITED_TMS

from .scoring import (
    ALL_TYPES,
    PHYSICAL_TYPES,
    compute_scores,
    estimate_damage,
    filter_dominated_moves,
    has_4x_weakness,
    SE_CHART,
)
from .solver import Params, optimise

DATA_PATH = (
    Path(__file__).parent.parent / "data" / "compiled" / "firered-leafgreen.json"
)


def load_pokemon(
    path: Path, no_legendaries: bool, no_4x_weakness: bool = False
) -> list[dict]:
    """Load compiled JSON and filter to fully-evolved Pokémon."""
    data = json.loads(path.read_text())
    pool = [p for p in data["pokemon"] if p["is_fully_evolved"]]
    if no_legendaries:
        pool = [p for p in pool if not p["is_legendary"]]
    if no_4x_weakness:
        pool = [p for p in pool if not has_4x_weakness(p["types"])]
    return pool


def display_team(team, pokemon_pool, scores):
    """Print the team roster, movesets, and type coverage matrix."""
    poke_by_name = {p["name"]: p for p in pokemon_pool}

    print("\n" + "=" * 72)
    print("  OPTIMAL TEAM")
    print("=" * 72)

    # -- Roster --
    print(f"\n{'Name':<14} {'Types':<20} {'Atk':>5} {'SpAtk':>5}")
    print("-" * 48)
    for entry in team:
        p = poke_by_name[entry["name"]]
        types_str = "/".join(t.capitalize() for t in p["types"])
        atk = p["base_stats"]["attack"]
        spa = p["base_stats"]["special-attack"]
        print(f"{p['name'].capitalize():<14} {types_str:<20} {atk:>5} {spa:>5}")

    # -- Movesets --
    print()
    move_by_name: dict[str, dict] = {}
    for p in pokemon_pool:
        for m in p["moves"]:
            if m["name"] not in move_by_name:
                move_by_name[m["name"]] = m

    for entry in team:
        p = poke_by_name[entry["name"]]
        print(f"\n  {p['name'].upper()}")
        print(
            f"  {'Move':<20} {'Type':<10} {'Pwr':>5} {'Acc':>5} {'Cat':<8} {'Best Dmg':>8}"
        )
        print("  " + "-" * 62)
        for mname in entry["moves"]:
            md = move_by_name.get(mname, {})
            m_type = md.get("type", "?")
            raw_power = md.get("power", 0) or 0
            multi_hit = md.get("multi_hit", 1.0)
            is_mt = md.get("is_multi_turn", False)
            effective_power = raw_power * multi_hit
            power_adj = effective_power / 2 if is_mt else effective_power
            acc = md.get("accuracy") or 100
            cat = "Phys" if m_type in PHYSICAL_TYPES else "Spec"
            atk_base = (
                p["base_stats"]["attack"]
                if m_type in PHYSICAL_TYPES
                else p["base_stats"]["special-attack"]
            )

            best_damage = max(
                (
                    estimate_damage(effective_power, atk_base, m_type, p["types"], t)
                    for t in ALL_TYPES
                ),
                default=0,
            )
            suffix = "*" if is_mt else ("×" if multi_hit > 1 else "")
            power_display = f"{power_adj:.0f}{suffix}"
            print(
                f"  {mname:<20} {m_type.capitalize():<10} {power_display:>5} "
                f"{acc:>5} {cat:<8} {best_damage:>8.0f}"
            )

    # -- Coverage matrix --
    print("\n\n  TYPE COVERAGE MATRIX  (best damage per cell, 0 = no damaging move)")
    print()

    short_names = [e["name"][:8] for e in team]
    header = (
        f"  {'Def Type':<12}"
        + "".join(f"{n:>10}" for n in short_names)
        + f"{'BEST':>10}"
    )
    print(header)
    print("  " + "-" * (12 + 10 * (len(team) + 1)))

    type_totals = {}
    weakest_type = None
    weakest_val = float("inf")

    for t in ALL_TYPES:
        row_vals = []
        for entry in team:
            p_name = entry["name"]
            p = poke_by_name[p_name]
            best = 0.0
            for mname in entry["moves"]:
                md = move_by_name.get(mname, {})
                m_type = md.get("type")
                m_power = md.get("power", 0) or 0
                if not m_type or m_power <= 0:
                    continue
                effective_power = m_power * md.get("multi_hit", 1.0)
                atk_base = (
                    p["base_stats"]["attack"]
                    if m_type in PHYSICAL_TYPES
                    else p["base_stats"]["special-attack"]
                )
                dmg = estimate_damage(effective_power, atk_base, m_type, p["types"], t)
                if dmg > best:
                    best = dmg
            row_vals.append(best)

        row_best = max(row_vals) if row_vals else 0.0
        type_totals[t] = row_best

        if row_best < weakest_val:
            weakest_val = row_best
            weakest_type = t

        cells = "".join(f"{v:>10.0f}" if v > 0 else f"{'—':>10}" for v in row_vals)
        print(f"  {t.capitalize():<12}{cells}{row_best:>10.0f}")

    print()
    if weakest_type:
        print(
            f"  Weakest link: {weakest_type.capitalize()} (best attacker damage = {weakest_val:.0f})"
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Gen 3 Pokémon Team Optimizer (FireRed/LeafGreen)"
    )
    parser.add_argument(
        "--max-overlap",
        type=int,
        default=3,
        help="Max Pokémon sharing any single type (default: 3)",
    )
    parser.add_argument(
        "--min-redundancy",
        type=int,
        default=1,
        help="Min SE (Pokémon, move) pairs per defending type (default: 1)",
    )
    parser.add_argument(
        "--max-same-type-moves",
        type=int,
        default=2,
        help="Max moves of the same attacking type per Pokémon (default: 2)",
    )
    parser.add_argument(
        "--min-role-types",
        type=int,
        default=1,
        help="Min defending types each selected Pokémon must cover as a role-holder (default: 1)",
    )
    parser.add_argument(
        "--role-threshold-pct",
        type=float,
        default=80.0,
        help="A role counts only if the chosen move is super-effective and within this percent of the best score for that defending type (default: 80)",
    )
    parser.add_argument(
        "--no-legendaries",
        action="store_true",
        default=True,
        help="Exclude legendaries (default: True)",
    )
    parser.add_argument(
        "--allow-legendaries",
        action="store_true",
        help="Allow legendaries in the pool",
    )
    parser.add_argument(
        "--no-4x-weakness",
        action="store_true",
        default=False,
        help="Exclude Pokémon with any 4x type weakness",
    )
    parser.add_argument(
        "--lock",
        action="append",
        default=[],
        metavar="POKEMON",
        help="Lock a Pokémon onto the team (repeatable)",
    )
    parser.add_argument(
        "--lock-move",
        action="append",
        nargs=2,
        default=[],
        metavar=("POKEMON", "MOVE"),
        help="Lock a move on a locked Pokémon (repeatable)",
    )
    parser.add_argument(
        "--must-have",
        action="append",
        default=[],
        metavar="MOVE",
        help="Require at least one Pokémon to carry this move (repeatable)",
    )
    parser.add_argument(
        "--must-have-type",
        action="append",
        default=[],
        metavar="TYPE",
        help="Require at least one Pokémon of this type on the team (repeatable)",
    )
    parser.add_argument(
        "--acc-exponent",
        type=float,
        default=2.0,
        help="Accuracy penalty exponent: (acc/100)^exp. Higher = harsher on low-acc (default: 2.0)",
    )
    parser.add_argument(
        "--speed-bonus",
        type=float,
        default=0.25,
        help="Max speed bonus for the fastest Pokémon (0.25=25%%, slowest gets 1.0x, default: 0.25)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="POKEMON",
        help="Exclude a Pokémon from the pool (repeatable)",
    )
    parser.add_argument(
        "--data",
        type=str,
        default=None,
        help="Path to compiled JSON (default: data/compiled/firered-leafgreen.json)",
    )

    args = parser.parse_args()

    no_legendaries = not args.allow_legendaries
    data_path = Path(args.data) if args.data else DATA_PATH

    locked_pokemon: dict[str, list[str]] = {}
    for name in args.lock:
        locked_pokemon.setdefault(name.lower(), [])
    for name, move in args.lock_move:
        locked_pokemon.setdefault(name.lower(), []).append(move.lower())

    params = Params(
        max_overlap=args.max_overlap,
        min_redundancy=args.min_redundancy,
        max_same_type_moves=args.max_same_type_moves,
        min_role_types=args.min_role_types,
        role_threshold_pct=args.role_threshold_pct,
        no_legendaries=no_legendaries,
        locked_pokemon=locked_pokemon,
        must_have_moves=[m.lower() for m in args.must_have],
        must_have_types=[t.lower() for t in args.must_have_type],
        unlimited_tms=FRLG_UNLIMITED_TMS,
    )

    excluded = {n.lower() for n in args.exclude}

    print(f"Loading data from {data_path}...")
    no_4x = getattr(args, "no_4x_weakness", False)
    pool = load_pokemon(data_path, params.no_legendaries, no_4x_weakness=no_4x)
    if excluded:
        pool = [p for p in pool if p["name"] not in excluded]
    print(f"Pool: {len(pool)} fully-evolved Pokémon")

    pool = filter_dominated_moves(
        pool,
        protected_moves_by_pokemon={
            name: set(moves) for name, moves in params.locked_pokemon.items()
        },
    )
    print("Pre-computing scores...")
    scores = compute_scores(
        pool,
        acc_exponent=args.acc_exponent,
        speed_bonus=args.speed_bonus,
    )
    print(f"Score entries: {len(scores)} (acc exponent: {args.acc_exponent})")

    print("\n--- Optimising (lexicographic max-min) ---")
    status, result, z_val, _obj_val = optimise(pool, scores, params)

    if status != "Optimal":
        print(f"\n{result}")
        return

    print(f"Min coverage (z) = {z_val:.1f}")
    display_team(result, pool, scores)


if __name__ == "__main__":
    main()
