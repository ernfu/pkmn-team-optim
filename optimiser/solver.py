"""
Lexicographic MILP solver for Gen 3 team optimisation using PuLP.

Stage 1 maximises z, a lower bound on single-attacker damage for every
defending type across all non-immune matchups. Stage 2 minimises duplicate move types both within each
Pokémon and across the team. Stage 3 breaks remaining ties in favour of
higher total firepower.
"""

from collections import defaultdict
from dataclasses import dataclass, field
import time

import pulp

from .scoring import ALL_TYPES, is_super_effective

PIN_TOLERANCE = 1e-6
SOLVER_TIME_LIMIT_SECONDS = 600


@dataclass
class Params:
    max_overlap: int = 3
    min_redundancy: int = 1
    max_same_type_moves: int = 2
    min_role_types: int = 1
    role_threshold_pct: float = 80.0
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
    u: dict
    z: pulp.LpVariable
    poke_names: list[str]
    moves_by_poke: dict[str, list[str]]
    total_power: pulp.LpAffineExpression
    within_duplicates: dict
    team_duplicates: dict
    diversity_penalty: pulp.LpAffineExpression


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


def _build_role_qualifiers(
    poke_names, moves_by_poke, move_type_of, scores, role_threshold_pct
):
    """Return qualifying moves for each (pokemon, defending_type) role.

    A move qualifies if it is super-effective against the defending type and
    its precomputed score is at least the configured percentage of the global
    best score for that defending type.
    """
    pct = max(0.0, min(role_threshold_pct, 100.0)) / 100.0
    best_score_by_type = {t: 0.0 for t in ALL_TYPES}

    for (_, _, def_type), score in scores.items():
        if score > best_score_by_type[def_type]:
            best_score_by_type[def_type] = score

    qualifying: dict[tuple[str, str], list[str]] = {}
    for p in poke_names:
        for def_type in ALL_TYPES:
            threshold = pct * best_score_by_type[def_type]
            moves = [
                m
                for m in moves_by_poke[p]
                if is_super_effective(move_type_of[p][m], def_type)
                and scores.get((p, m, def_type), 0) > 0
                and scores.get((p, m, def_type), 0) >= threshold
            ]
            if moves:
                qualifying[p, def_type] = moves

    return qualifying


def _solve_with_current_objective(
    prob, solver_kwargs, progress_fn=None, elapsed_offset=0.0, stage_label=""
):
    """Solve the current model objective and return (success, elapsed_seconds)."""
    run_kwargs = dict(solver_kwargs)

    if progress_fn is not None:
        import highspy

        last_t = [0.0]

        def _cb(callback_type, message, data_out, data_in, user_data):
            t = data_out.running_time
            if t - last_t[0] < 0.5:
                return
            last_t[0] = t
            progress_fn(
                stage_label,
                data_out.mip_gap,
                data_out.mip_node_count,
                elapsed_offset + t,
            )

        run_kwargs["callbackTuple"] = (_cb, None)
        run_kwargs["callbacksToActivate"] = [
            highspy.cb.HighsCallbackType.kCallbackMipInterrupt,
        ]

    start = time.perf_counter()
    prob.solve(pulp.HiGHS(**run_kwargs))
    elapsed = time.perf_counter() - start
    return prob.status == pulp.constants.LpStatusOptimal, elapsed


def build_model(pokemon_pool, scores, params):
    """
    Build the MILP model without solving it.

    Returns a Model handle to pass to solve_model.
    """
    idx = _build_index(pokemon_pool, scores, params.unlimited_tms)
    poke_names, poke_by_name, moves_by_poke, move_type_of, single_use_tm_users = idx
    role_qualifiers = _build_role_qualifiers(
        poke_names, moves_by_poke, move_type_of, scores, params.role_threshold_pct
    )

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
    prob += z

    # -- structural --
    prob += pulp.lpSum(x[p] for p in poke_names) == 6, "team_size"

    for p in poke_names:
        prob += (
            pulp.lpSum(y[p, m] for m in moves_by_poke[p]) == 4 * x[p],
            f"exact_moves_{p}",
        )
        for m in moves_by_poke[p]:
            prob += y[p, m] <= x[p], f"move_req_{p}_{m}"

    # -- move-type diversity bookkeeping --
    within_duplicates = {}
    team_type_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for p in poke_names:
        type_groups: dict[str, list[str]] = defaultdict(list)
        for m in moves_by_poke[p]:
            mtype = move_type_of[p][m]
            type_groups[mtype].append(m)
            team_type_groups[mtype].append((p, m))

        for mtype, mlist in type_groups.items():
            if (
                params.max_same_type_moves < 4
                and len(mlist) > params.max_same_type_moves
            ):
                prob += (
                    pulp.lpSum(y[p, m] for m in mlist) <= params.max_same_type_moves,
                    f"type_cap_{p}_{mtype}",
                )

            if len(mlist) < 2:
                continue

            dup_var = pulp.LpVariable(f"dup_within_{p}_{mtype}", lowBound=0)
            prob += (
                dup_var >= pulp.lpSum(y[p, m] for m in mlist) - 1,
                f"dup_within_lb_{p}_{mtype}",
            )
            within_duplicates[p, mtype] = dup_var

    team_duplicates = {}
    for mtype, pairs in team_type_groups.items():
        dup_var = pulp.LpVariable(f"dup_team_{mtype}", lowBound=0)
        prob += (
            dup_var >= pulp.lpSum(y[p, m] for p, m in pairs) - 1,
            f"dup_team_lb_{mtype}",
        )
        team_duplicates[mtype] = dup_var

    diversity_penalty = pulp.lpSum(within_duplicates.values()) + pulp.lpSum(
        team_duplicates.values()
    )

    # -- action-selection: each Pokémon uses at most one move per matchup --
    w = {}
    damaging_moves_by_poke_type: dict[tuple[str, str], list[str]] = defaultdict(list)

    for p in poke_names:
        for m in moves_by_poke[p]:
            for t in ALL_TYPES:
                if (p, m, t) in scores:
                    w[p, m, t] = pulp.LpVariable(f"w_{p}_{m}_{t}", 0, 1)
                    prob += w[p, m, t] <= y[p, m], f"w_req_{p}_{m}_{t}"
                    damaging_moves_by_poke_type[p, t].append(m)

    for (p, t), mlist in damaging_moves_by_poke_type.items():
        if len(mlist) >= 2:
            prob += (
                pulp.lpSum(w[p, m, t] for m in mlist) <= 1,
                f"one_move_{p}_{t}",
            )

    # -- single-attacker: only one Pokémon contributes damage per type --
    u = {}
    pokes_with_damage: dict[str, list[str]] = defaultdict(list)

    for p, t in damaging_moves_by_poke_type:
        u[p, t] = pulp.LpVariable(f"u_{p}_{t}", cat="Binary")
        prob += u[p, t] <= x[p], f"u_on_team_{p}_{t}"
        pokes_with_damage[t].append(p)

    for t, plist in pokes_with_damage.items():
        prob += (
            pulp.lpSum(u[p, t] for p in plist) <= 1,
            f"one_attacker_{t}",
        )

    for p, m, t in w:
        prob += w[p, m, t] <= u[p, t], f"w_att_{p}_{m}_{t}"

    # Role-qualified moves always define who may be the designated attacker.
    # The optional min_role_types quota only controls how many such roles each
    # selected Pokémon must own; it does not disable this filter.
    for p, t in damaging_moves_by_poke_type:
        qualifying_moves = role_qualifiers.get((p, t), [])
        if qualifying_moves:
            prob += (
                u[p, t] <= pulp.lpSum(w[p, m, t] for m in qualifying_moves),
                f"u_role_link_{p}_{t}",
            )
        else:
            prob += u[p, t] == 0, f"u_role_forbidden_{p}_{t}"

    if params.min_role_types > 0:
        for p in poke_names:
            prob += (
                pulp.lpSum(u[p, t] for t in ALL_TYPES if (p, t) in u)
                >= params.min_role_types * x[p],
                f"min_role_types_{p}",
            )

    # -- min coverage per type (z is the worst-case lower bound) --
    for t in ALL_TYPES:
        terms = []
        for p in poke_names:
            for m in moves_by_poke[p]:
                if (p, m, t) in w:
                    terms.append(scores[p, m, t] * w[p, m, t])
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
            if is_super_effective(move_type_of[p][m], t)
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
        prob=prob,
        x=x,
        y=y,
        u=u,
        z=z,
        poke_names=poke_names,
        moves_by_poke=moves_by_poke,
        total_power=total_power,
        within_duplicates=within_duplicates,
        team_duplicates=team_duplicates,
        diversity_penalty=diversity_penalty,
    )


def solve_model(model, progress_fn=None):
    """
    Solve a built Model.

    Returns (status, team_list | error_message, z_value, power_value).

    If *progress_fn* is supplied it is called with
    (stage_label, mip_gap, node_count, running_time).
    """
    solver_kwargs = dict(msg=0, gapRel=0, timeLimit=SOLVER_TIME_LIMIT_SECONDS)

    elapsed_total = 0.0

    model.prob.setObjective(model.z)
    if progress_fn is not None:
        progress_fn("Stage 1/3: Maximize coverage", None, None, elapsed_total)
    ok, elapsed = _solve_with_current_objective(
        model.prob,
        solver_kwargs,
        progress_fn=progress_fn,
        elapsed_offset=elapsed_total,
        stage_label="Stage 1/3: Maximize coverage",
    )
    elapsed_total += elapsed
    if not ok:
        return "Infeasible", None, 0, 0

    z_star = pulp.value(model.z)
    model.prob += model.z >= z_star - PIN_TOLERANCE, "pin_z_star"

    model.prob.sense = pulp.LpMinimize
    model.prob.setObjective(model.diversity_penalty)
    if progress_fn is not None:
        progress_fn("Stage 2/3: Minimize duplicates", None, None, elapsed_total)
    ok, elapsed = _solve_with_current_objective(
        model.prob,
        solver_kwargs,
        progress_fn=progress_fn,
        elapsed_offset=elapsed_total,
        stage_label="Stage 2/3: Minimize duplicates",
    )
    elapsed_total += elapsed
    if not ok:
        return "Infeasible", None, 0, 0

    diversity_star = pulp.value(model.diversity_penalty)
    model.prob += (
        model.diversity_penalty <= diversity_star + PIN_TOLERANCE,
        "pin_diversity_star",
    )

    model.prob.sense = pulp.LpMaximize
    model.prob.setObjective(model.total_power)
    if progress_fn is not None:
        progress_fn("Stage 3/3: Maximize firepower", None, None, elapsed_total)
    ok, elapsed = _solve_with_current_objective(
        model.prob,
        solver_kwargs,
        progress_fn=progress_fn,
        elapsed_offset=elapsed_total,
        stage_label="Stage 3/3: Maximize firepower",
    )
    if not ok:
        return "Infeasible", None, 0, 0

    team = []
    for p in model.poke_names:
        if pulp.value(model.x[p]) > 0.5:
            chosen_moves = [
                m for m in model.moves_by_poke[p] if pulp.value(model.y[p, m]) > 0.5
            ]
            team.append({"name": p, "moves": chosen_moves})

    z_val = pulp.value(model.z)
    power_val = pulp.value(model.total_power)
    return "Optimal", team, z_val, power_val


def optimise(pokemon_pool, scores, params):
    """Convenience wrapper: build + solve in one call."""
    model = build_model(pokemon_pool, scores, params)
    return solve_model(model)


def _diagnose_infeasibility(
    params, *, no_4x_weakness: bool = False, excluded_pokemon=None
):
    suggestions = []
    if params.min_redundancy >= 2:
        suggestions.append(
            "Lower 'Min SE Backups Per Type' in "
            "'Optimize Settings > Advanced Settings' "
            f"(currently {params.min_redundancy})"
        )
    if params.max_same_type_moves < 4:
        suggestions.append(
            "Raise 'Max Repeated Move Type' in "
            "'Optimize Settings > Advanced Settings' "
            f"(currently {params.max_same_type_moves})"
        )
    if params.min_role_types > 0:
        suggestions.append(
            "Lower 'Min Assigned Roles' in "
            "'Optimize Settings > Advanced Settings' "
            f"(currently {params.min_role_types})"
        )
    if params.role_threshold_pct > 0:
        suggestions.append(
            "Lower 'Role Strength Threshold (% of Best)' in "
            "'Optimize Settings > Advanced Settings' "
            f"(currently {params.role_threshold_pct:.0f}%)"
        )
    if params.max_overlap < 6:
        suggestions.append(
            "Raise 'Max Shared Team Type' in "
            "'Optimize Settings > Advanced Settings' "
            f"(currently {params.max_overlap})"
        )
    if params.locked_pokemon:
        suggestions.append(
            "Remove some entries from 'Team Constraints > Lock Pokemon' or "
            "'Team Constraints > Lock Moves'"
        )
    if params.must_have_moves:
        suggestions.append(
            "Remove some entries from 'Team Constraints > Must-Have Moves'"
        )
    if params.must_have_types:
        suggestions.append(
            "Remove some entries from 'Team Constraints > Must-Have Types'"
        )
    if excluded_pokemon:
        suggestions.append(
            "Remove some entries from 'Team Constraints > Exclude Pokemon'"
        )
    if no_4x_weakness:
        suggestions.append(
            "Turn off 'Team Constraints > Exclude 4x Weakness Pokemon'"
        )
    if not suggestions:
        suggestions.append(
            "Relax one of the controls in 'Optimize Settings' or remove a restrictive "
            "entry from 'Team Constraints'"
        )
    msg = "Optimisation is infeasible. Suggestions:\n" + "\n".join(
        f"  - {s}" for s in suggestions
    )
    return "Infeasible", msg, 0
