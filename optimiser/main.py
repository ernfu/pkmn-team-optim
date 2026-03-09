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
            f"  {'Move':<20} {'Type':<10} {'Pwr':>5} {'Acc':>5} {'Cat':<8} {'Best SE':>8}"
        )
        print("  " + "-" * 62)
        for mname in entry["moves"]:
            md = move_by_name.get(mname, {})
            m_type = md.get("type", "?")
            raw_power = md.get("power", 0) or 0
            is_mt = md.get("is_multi_turn", False)
            power_adj = raw_power // 2 if is_mt else raw_power
            acc = md.get("accuracy") or 100
            cat = "Phys" if m_type in PHYSICAL_TYPES else "Spec"

            best_se = max(
                (scores.get((p["name"], mname, t), 0) for t in ALL_TYPES),
                default=0,
            )
            power_display = f"{power_adj}{'*' if is_mt else ''}"
            print(
                f"  {mname:<20} {m_type.capitalize():<10} {power_display:>5} "
                f"{acc:>5} {cat:<8} {best_se:>8.0f}"
            )

    # -- Coverage matrix --
    print("\n\n  TYPE COVERAGE MATRIX  (best SE score per cell, 0 = no SE move)")
    print()

    short_names = [e["name"][:8] for e in team]
    header = (
        f"  {'Def Type':<12}"
        + "".join(f"{n:>10}" for n in short_names)
        + f"{'TOTAL':>10}"
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
            best = 0.0
            for mname in entry["moves"]:
                s = scores.get((p_name, mname, t), 0)
                if s > best:
                    best = s
            row_vals.append(best)

        row_total = sum(row_vals)
        type_totals[t] = row_total

        if row_total < weakest_val:
            weakest_val = row_total
            weakest_type = t

        cells = "".join(f"{v:>10.0f}" if v > 0 else f"{'—':>10}" for v in row_vals)
        print(f"  {t.capitalize():<12}{cells}{row_total:>10.0f}")

    print()
    if weakest_type:
        print(
            f"  Weakest link: {weakest_type.capitalize()} (total SE score = {weakest_val:.0f})"
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Gen 3 Pokémon Team Optimizer (FireRed/LeafGreen)"
    )
    parser.add_argument(
        "--max-overlap",
        type=int,
        default=1,
        help="Max Pokémon sharing any single type (default: 1)",
    )
    parser.add_argument(
        "--min-redundancy",
        type=int,
        default=2,
        help="Min SE (Pokémon, move) pairs per defending type (default: 2)",
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
        "--duplicate-type-discount",
        type=float,
        default=0.2,
        help="How much a 2nd move of the same type counts (0=avoid, 1=full value, default: 0.2)",
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
        duplicate_type_discount=args.duplicate_type_discount,
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

    print("Pre-computing scores...")
    scores = compute_scores(
        pool,
        acc_exponent=args.acc_exponent,
        speed_bonus=args.speed_bonus,
    )
    print(f"Score entries: {len(scores)} (acc exponent: {args.acc_exponent})")

    print("\n--- Optimising (regularised max-min) ---")
    status, result, z_val = optimise(pool, scores, params)

    if status != "Optimal":
        print(f"\n{result}")
        return

    print(f"Min coverage (z) = {z_val:.1f}")
    display_team(result, pool, scores)


if __name__ == "__main__":
    main()
