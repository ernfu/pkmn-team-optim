"""
Single-phase MILP solver for Gen 3 team optimisation using PuLP.

Regularised max-min: maximise z + ε·total_power, where z is a lower bound
on coverage for every defending type.  This finds the best worst-case
coverage and breaks ties in favour of higher total firepower.
"""

from collections import defaultdict
from dataclasses import dataclass, field

import pulp

from .scoring import ALL_TYPES

EPSILON = 1e-4


@dataclass
class Params:
    max_overlap: int = 1
    min_redundancy: int = 2
    duplicate_type_discount: float = 0.2
    no_legendaries: bool = True
    locked_pokemon: dict[str, list[str]] = field(default_factory=dict)
    must_have_moves: list[str] = field(default_factory=list)
    must_have_types: list[str] = field(default_factory=list)
    unlimited_tms: set[str] = field(default_factory=set)


@dataclass
class Model:
    """Opaque handle returned by build_model, consumed by solve_model."""

    prob: pulp.LpProblem
    x: dict
    y: dict
    z: pulp.LpVariable
    poke_names: list[str]
    moves_by_poke: dict[str, list[str]]


def _build_index(pokemon_pool, scores, unlimited_tms):
    """Build helper indexes used by both phases."""
    poke_names = [p["name"] for p in pokemon_pool]
    poke_by_name = {p["name"]: p for p in pokemon_pool}

    moves_by_poke: dict[str, list[str]] = {}
    move_type_of: dict[str, dict[str, str]] = {}

    # single_use_tm_users[move_name] = list of pokemon that need the TM
    single_use_tm_users: dict[str, list[str]] = defaultdict(list)

    for p in pokemon_pool:
        atk_moves = []
        type_map: dict[str, str] = {}
        seen = set()
        for m in p["moves"]:
            if m["power"] and m["power"] > 0 and m["name"] not in seen:
                atk_moves.append(m["name"])
                type_map[m["name"]] = m["type"]
                seen.add(m["name"])
                if m.get("tm_only") and m["name"] not in unlimited_tms:
                    single_use_tm_users[m["name"]].append(p["name"])
        moves_by_poke[p["name"]] = atk_moves
        move_type_of[p["name"]] = type_map

    return (
        poke_names,
        poke_by_name,
        moves_by_poke,
        move_type_of,
        dict(single_use_tm_users),
    )


def _add_tm_constraints(prob, y, single_use_tm_users):
    """Single-use TM uniqueness constraints."""
    for tm_move, users in single_use_tm_users.items():
        if len(users) >= 2:
            prob += (
                pulp.lpSum(y[p, tm_move] for p in users if (p, tm_move) in y) <= 1,
                f"single_tm_{tm_move}",
            )


def build_model(pokemon_pool, scores, params):
    """
    Build the MILP model without solving it.

    Returns a Model handle to pass to solve_model.
    """
    idx = _build_index(pokemon_pool, scores, params.unlimited_tms)
    poke_names, poke_by_name, moves_by_poke, move_type_of, single_use_tm_users = idx

    prob = pulp.LpProblem("TeamOptimiser", pulp.LpMaximize)

    x = {p: pulp.LpVariable(f"x_{p}", cat="Binary") for p in poke_names}
    y = {}
    for p in poke_names:
        for m in moves_by_poke[p]:
            y[p, m] = pulp.LpVariable(f"y_{p}_{m}", cat="Binary")
    z = pulp.LpVariable("z")

    total_power = pulp.lpSum(
        scores.get((p, m, t), 0) * y[p, m]
        for t in ALL_TYPES
        for p in poke_names
        for m in moves_by_poke[p]
    )
    prob += z + EPSILON * total_power

    # -- structural --
    prob += pulp.lpSum(x[p] for p in poke_names) == 6, "team_size"

    for p in poke_names:
        prob += (
            pulp.lpSum(y[p, m] for m in moves_by_poke[p]) <= 4 * x[p],
            f"max_moves_{p}",
        )
        for m in moves_by_poke[p]:
            prob += y[p, m] <= x[p], f"move_req_{p}_{m}"

    # -- move type diversity: at most 1 same-type move gets full credit --
    discount = params.duplicate_type_discount
    full = {}

    if discount < 1.0:
        for p in poke_names:
            type_groups: dict[str, list[str]] = defaultdict(list)
            for m in moves_by_poke[p]:
                type_groups[move_type_of[p][m]].append(m)
            for mtype, mlist in type_groups.items():
                if len(mlist) >= 2:
                    for m in mlist:
                        full[p, m] = pulp.LpVariable(f"full_{p}_{m}", 0, 1)
                        prob += full[p, m] <= y[p, m], f"full_req_{p}_{m}"
                    prob += (
                        pulp.lpSum(full[p, m] for m in mlist) <= 1,
                        f"one_full_{p}_{mtype}",
                    )

    # -- min coverage per type (z is the worst-case lower bound) --
    for t in ALL_TYPES:
        terms = []
        for p in poke_names:
            for m in moves_by_poke[p]:
                s = scores.get((p, m, t), 0)
                if s == 0:
                    continue
                if (p, m) in full:
                    terms.append(
                        discount * s * y[p, m] + (1 - discount) * s * full[p, m]
                    )
                else:
                    terms.append(s * y[p, m])
        prob += z <= pulp.lpSum(terms), f"min_cov_{t}"

    # -- type overlap cap --
    for t in ALL_TYPES:
        pokes_of_type = [p for p in poke_names if t in poke_by_name[p]["types"]]
        if pokes_of_type:
            prob += (
                pulp.lpSum(x[p] for p in pokes_of_type) <= params.max_overlap,
                f"type_overlap_{t}",
            )

    # -- SE redundancy --
    for t in ALL_TYPES:
        se_pairs = pulp.lpSum(
            y[p, m]
            for p in poke_names
            for m in moves_by_poke[p]
            if scores.get((p, m, t), 0) > 0
        )
        prob += se_pairs >= params.min_redundancy, f"se_redundancy_{t}"

    # -- TM uniqueness --
    _add_tm_constraints(prob, y, single_use_tm_users)

    # -- user constraints --
    for locked_name, locked_moves in params.locked_pokemon.items():
        if locked_name in x:
            prob += x[locked_name] == 1, f"lock_poke_{locked_name}"
            for lm in locked_moves:
                if (locked_name, lm) in y:
                    prob += y[locked_name, lm] == 1, f"lock_move_{locked_name}_{lm}"

    for must_move in params.must_have_moves:
        carriers = [
            y[p, m] for p in poke_names for m in moves_by_poke[p] if m == must_move
        ]
        if carriers:
            prob += pulp.lpSum(carriers) >= 1, f"must_have_{must_move}"

    for must_type in params.must_have_types:
        pokes_of_type = [p for p in poke_names if must_type in poke_by_name[p]["types"]]
        if pokes_of_type:
            prob += (
                pulp.lpSum(x[p] for p in pokes_of_type) >= 1,
                f"must_have_type_{must_type}",
            )

    return Model(
        prob=prob, x=x, y=y, z=z,
        poke_names=poke_names, moves_by_poke=moves_by_poke,
    )


def solve_model(model):
    """
    Solve a built Model.

    Returns (status, team_list | error_message, z_value).
    """
    model.prob.solve(pulp.HiGHS(msg=0, gapRel=0.01, timeLimit=30))

    if model.prob.status != pulp.constants.LpStatusOptimal:
        return "Infeasible", None, 0

    team = []
    for p in model.poke_names:
        if pulp.value(model.x[p]) > 0.5:
            chosen_moves = [
                m for m in model.moves_by_poke[p]
                if pulp.value(model.y[p, m]) > 0.5
            ]
            team.append({"name": p, "moves": chosen_moves})

    return "Optimal", team, pulp.value(model.z)


def optimise(pokemon_pool, scores, params):
    """Convenience wrapper: build + solve in one call."""
    model = build_model(pokemon_pool, scores, params)
    return solve_model(model)


def _diagnose_infeasibility(params):
    suggestions = []
    if params.min_redundancy >= 2:
        suggestions.append(
            f"Lower min SE redundancy (currently k={params.min_redundancy})"
        )
    if params.max_overlap <= 2:
        suggestions.append(f"Raise max type overlap (currently n={params.max_overlap})")
    msg = "Optimisation is infeasible. Suggestions:\n" + "\n".join(
        f"  - {s}" for s in suggestions
    )
    return "Infeasible", msg, 0
