"""Flask web interface for the Gen 3 Pokemon Team Optimizer."""

from collections import defaultdict
import json
import logging
import math
import queue
import threading

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

from data.pokedex import (
    DEFAULT_VERSION_GROUP,
    compiled_path_for,
    get_profile,
    list_version_groups,
)
from optimiser.main import (
    DATA_PATH,
    dataset_generation,
    dataset_unlimited_tms,
    load_dataset,
    load_pokemon,
)
from optimiser.scoring import (
    attack_stat_for_move,
    compute_scores,
    estimate_damage,
    filter_dominated_moves,
    has_4x_weakness,
    move_category,
    types_for_generation,
)
from optimiser.solver import (
    SOLVER_TIME_LIMIT_SECONDS,
    Params,
    _diagnose_infeasibility,
    build_model,
    solve_model,
)

app = Flask(__name__)

_dataset_cache: dict[str, dict] = {}
_score_cache: dict[tuple, tuple] = {}


def _dataset_manifest() -> list[dict]:
    manifest = []
    for version_group in list_version_groups():
        profile = get_profile(version_group)
        path = compiled_path_for(version_group)
        is_national = profile.get("pokedex") == "national"
        game_key = profile.get("source_version_group", version_group)
        label = profile["label"]
        game_label = label.split(" (")[0] if " (" in label else label
        manifest.append(
            {
                "version_group": version_group,
                "label": label,
                "generation": profile["generation"],
                "game_key": game_key,
                "game_label": game_label,
                "dex_type": "national" if is_national else "regional",
                "available_types": types_for_generation(profile["generation"]),
                "compiled": path.exists(),
            }
        )
    return manifest


def _get_dataset_context(version_group: str) -> dict:
    if version_group not in _dataset_cache:
        path = compiled_path_for(version_group)
        if not path.exists():
            raise FileNotFoundError(
                f"Compiled dataset '{version_group}' not found at {path}. Run the compiler first."
            )
        dataset = load_dataset(path)
        all_pokemon = load_pokemon(dataset, no_legendaries=False)
        poke_by_name = {p["name"]: p for p in all_pokemon}
        move_lookup: dict[str, dict] = {}
        for pokemon in all_pokemon:
            for move in pokemon["moves"]:
                move_lookup.setdefault(move["name"], move)
        _dataset_cache[version_group] = {
            "dataset": dataset,
            "generation": dataset_generation(dataset),
            "all_pokemon": all_pokemon,
            "poke_by_name": poke_by_name,
            "move_lookup": move_lookup,
            "all_moves_sorted": sorted(
                name
                for name, move in move_lookup.items()
                if move.get("power") and move["power"] > 0
            ),
        }
    return _dataset_cache[version_group]


def _get_pool_and_scores(
    version_group: str,
    no_legendaries: bool,
    acc_exponent: float,
    speed_bonus: float = 0.25,
    no_4x_weakness: bool = False,
    protected_moves_by_pokemon: dict[str, tuple[str, ...]] | None = None,
):
    context = _get_dataset_context(version_group)
    generation = context["generation"]
    protected_moves_by_pokemon = protected_moves_by_pokemon or {}
    key = (
        version_group,
        no_legendaries,
        acc_exponent,
        speed_bonus,
        no_4x_weakness,
        tuple(
            sorted((name, moves) for name, moves in protected_moves_by_pokemon.items())
        ),
    )
    if key not in _score_cache:
        locked_names = set(protected_moves_by_pokemon) if protected_moves_by_pokemon else set()
        pool = (
            [p for p in context["all_pokemon"] if not p["is_legendary"] or p["name"] in locked_names]
            if no_legendaries
            else list(context["all_pokemon"])
        )
        if no_4x_weakness:
            pool = [
                p
                for p in pool
                if not has_4x_weakness(p["types"], generation=generation)
                or p["name"] in locked_names
            ]
        pool = filter_dominated_moves(
            pool,
            protected_moves_by_pokemon={
                name: set(moves) for name, moves in protected_moves_by_pokemon.items()
            },
        )
        scores = compute_scores(
            pool,
            acc_exponent=acc_exponent,
            speed_bonus=speed_bonus,
            generation=generation,
        )
        _score_cache[key] = (pool, scores)
    return _score_cache[key]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/datasets")
def api_datasets():
    return jsonify(
        {
            "default_version_group": DEFAULT_VERSION_GROUP,
            "datasets": _dataset_manifest(),
        }
    )


@app.route("/api/pokemon")
def api_pokemon():
    version_group = request.args.get("version_group", DEFAULT_VERSION_GROUP)
    context = _get_dataset_context(version_group)
    return jsonify(sorted(p["name"] for p in context["all_pokemon"]))


@app.route("/api/moves/<pokemon>")
def api_moves(pokemon):
    version_group = request.args.get("version_group", DEFAULT_VERSION_GROUP)
    context = _get_dataset_context(version_group)
    poke = context["poke_by_name"].get(pokemon.lower())
    if not poke:
        return jsonify({"error": f"Pokemon '{pokemon}' not found"}), 404
    moves, seen = [], set()
    for m in poke["moves"]:
        if m["power"] and m["power"] > 0 and m["name"] not in seen:
            moves.append(m["name"])
            seen.add(m["name"])
    return jsonify(sorted(moves))


@app.route("/api/all-moves")
def api_all_moves():
    version_group = request.args.get("version_group", DEFAULT_VERSION_GROUP)
    context = _get_dataset_context(version_group)
    return jsonify(context["all_moves_sorted"])


def _sse(event, data):
    """Format a single Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.route("/optimize-stream", methods=["POST"])
def optimize_stream():
    """SSE endpoint that streams solver progress then the final result."""
    data = request.get_json()
    version_group = data.get("version_group", DEFAULT_VERSION_GROUP)
    context = _get_dataset_context(version_group)
    profile = get_profile(version_group)
    generation = context["generation"]

    max_overlap = int(data.get("max_overlap", 3))
    min_redundancy = int(data.get("min_redundancy", 1))
    max_same_type_moves = int(data.get("max_same_type_moves", 4))
    min_role_types = int(data.get("min_role_types", 1))
    role_threshold_pct = float(data.get("role_threshold_pct", 80.0))
    acc_exponent = float(data.get("acc_exponent", 2.0))
    speed_bonus = float(data.get("speed_bonus", 0.25))
    allow_legendaries = bool(data.get("allow_legendaries", False))
    no_4x_weakness = bool(data.get("no_4x_weakness", False))
    must_include_starter = bool(data.get("must_include_starter", False))

    locked_pokemon: dict[str, list[str]] = {}
    for name in data.get("locked_pokemon", []):
        locked_pokemon.setdefault(name.lower(), [])
    for pair in data.get("locked_moves", []):
        locked_pokemon.setdefault(pair["pokemon"].lower(), []).append(
            pair["move"].lower()
        )

    params = Params(
        max_overlap=max_overlap,
        min_redundancy=min_redundancy,
        max_same_type_moves=max_same_type_moves,
        min_role_types=min_role_types,
        role_threshold_pct=role_threshold_pct,
        no_legendaries=not allow_legendaries,
        locked_pokemon=locked_pokemon,
        must_have_moves=[m.lower() for m in data.get("must_have_moves", [])],
        must_have_types=[t.lower() for t in data.get("must_have_types", [])],
        must_include_any_of_pokemon=(
            sorted(profile.get("starter_candidates", []))
            if must_include_starter
            else []
        ),
        unlimited_tms=dataset_unlimited_tms(context["dataset"]),
    )

    excluded_pokemon = {n.lower() for n in data.get("excluded_pokemon", [])}

    pool, scores = _get_pool_and_scores(
        version_group,
        params.no_legendaries,
        acc_exponent,
        speed_bonus,
        no_4x_weakness,
        protected_moves_by_pokemon={
            name: tuple(sorted(moves)) for name, moves in locked_pokemon.items()
        },
    )

    if excluded_pokemon:
        pool = [p for p in pool if p["name"] not in excluded_pokemon]

    def generate():
        yield _sse("phase", {"phase": "building"})

        model = build_model(pool, scores, params, generation=generation)
        yield _sse("phase", {"phase": "solving"})

        progress_q = queue.Queue()
        result_holder = [None]
        total_time_limit = SOLVER_TIME_LIMIT_SECONDS * 3

        def _run_solver():
            def _on_progress(stage, gap, nodes, elapsed):
                if gap is None and nodes is None:
                    progress_q.put(("phase", stage, round(elapsed, 2)))
                else:
                    progress_q.put(
                        ("progress", stage, gap, int(nodes), round(elapsed, 2))
                    )

            result_holder[0] = solve_model(model, progress_fn=_on_progress)
            progress_q.put(("done",))

        t = threading.Thread(target=_run_solver, daemon=True)
        t.start()

        displayed_pct = 0.0

        while True:
            try:
                msg = progress_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if msg[0] == "done":
                break
            if msg[0] == "phase":
                _, phase, elapsed = msg
                target = min(95.0, 95.0 * elapsed / total_time_limit)
                if target > displayed_pct:
                    displayed_pct = target
                yield _sse(
                    "progress",
                    {
                        "pct": round(displayed_pct, 1),
                        "gap": None,
                        "nodes": 0,
                        "time": elapsed,
                        "phase": phase,
                    },
                )
                continue

            _, phase, gap, nodes, elapsed = msg
            safe_gap = gap if math.isfinite(gap) else None

            target = min(95.0, 95.0 * elapsed / total_time_limit)

            if target > displayed_pct:
                displayed_pct = target

            yield _sse(
                "progress",
                {
                    "pct": round(displayed_pct, 1),
                    "gap": safe_gap,
                    "nodes": nodes,
                    "time": elapsed,
                    "phase": phase,
                },
            )

        status, result, z_val, power_val = result_holder[0]
        if status != "Optimal":
            _, diag_msg, _ = _diagnose_infeasibility(
                params,
                pokemon_pool=pool,
                no_4x_weakness=no_4x_weakness,
                excluded_pokemon=excluded_pokemon,
            )
            yield _sse("error", {"status": status, "message": diag_msg})
        else:
            yield _sse(
                "result",
                _build_result(
                    result,
                    pool,
                    scores,
                    z_val,
                    power_val,
                    generation=generation,
                    version_group=version_group,
                    label=context["dataset"]["label"],
                ),
            )

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _build_result(
    team,
    pool,
    scores,
    z_val,
    power_val=None,
    *,
    generation: int = 3,
    version_group: str | None = None,
    label: str | None = None,
):
    poke_by_name = {p["name"]: p for p in pool}
    move_by_name: dict[str, dict] = {}
    defending_types = types_for_generation(generation)
    for p in pool:
        for m in p["moves"]:
            move_by_name.setdefault(m["name"], m)

    roster = []
    for entry in team:
        p = poke_by_name[entry["name"]]
        roster.append(
            {
                "name": p["name"],
                "types": p["types"],
                "attack": p["base_stats"]["attack"],
                "special_attack": p["base_stats"]["special-attack"],
            }
        )

    movesets = []
    for entry in team:
        p = poke_by_name[entry["name"]]
        moves = []
        for mname in entry["moves"]:
            md = move_by_name.get(mname, {})
            m_type = md.get("type", "?")
            raw_power = md.get("power", 0) or 0
            multi_hit = md.get("multi_hit", 1.0)
            is_mt = md.get("is_multi_turn", False)
            effective_power = raw_power * multi_hit
            acc = md.get("accuracy") or 100
            atk_base = attack_stat_for_move(p, md, generation=generation)
            best_damage = max(
                (
                    estimate_damage(
                        effective_power,
                        atk_base,
                        m_type,
                        p["types"],
                        t,
                        generation=generation,
                    )
                    for t in defending_types
                ),
                default=0,
            )
            moves.append(
                {
                    "name": mname,
                    "type": m_type,
                    "power": effective_power / 2 if is_mt else effective_power,
                    "is_multi_turn": is_mt,
                    "accuracy": acc,
                    "category": move_category(md, generation=generation).title(),
                    "best_damage": best_damage,
                }
            )
        movesets.append({"pokemon": entry["name"], "moves": moves})

    within_duplicate_total = 0
    team_type_counts: dict[str, int] = defaultdict(int)
    for entry in team:
        poke_type_counts: dict[str, int] = defaultdict(int)
        for mname in entry["moves"]:
            m_type = move_by_name.get(mname, {}).get("type")
            if not m_type:
                continue
            poke_type_counts[m_type] += 1
            team_type_counts[m_type] += 1
        within_duplicate_total += sum(
            max(count - 1, 0) for count in poke_type_counts.values()
        )

    team_duplicate_total = sum(max(count - 1, 0) for count in team_type_counts.values())
    diversity_val = within_duplicate_total + team_duplicate_total

    if power_val is None:
        power_val = sum(
            scores.get((entry["name"], mname, t), 0)
            for t in defending_types
            for entry in team
            for mname in entry["moves"]
        )

    coverage = []
    weakest_type, weakest_val = None, float("inf")
    for t in defending_types:
        row = {"type": t, "cells": []}
        for entry in team:
            p = poke_by_name[entry["name"]]
            best = 0
            for mn in entry["moves"]:
                md = move_by_name.get(mn, {})
                m_type = md.get("type")
                m_power = md.get("power", 0) or 0
                if not m_type or m_power <= 0:
                    continue
                effective_power = m_power * md.get("multi_hit", 1.0)
                atk_base = attack_stat_for_move(p, md, generation=generation)
                dmg = estimate_damage(
                    effective_power,
                    atk_base,
                    m_type,
                    p["types"],
                    t,
                    generation=generation,
                )
                if dmg > best:
                    best = dmg
            row["cells"].append(best)
        row["total"] = max(row["cells"]) if row["cells"] else 0
        if row["total"] < weakest_val:
            weakest_val, weakest_type = row["total"], t
        coverage.append(row)

    return {
        "status": "Optimal",
        "version_group": version_group,
        "label": label,
        "generation": generation,
        "z_val": round(z_val, 1),
        "power_val": round(power_val, 1) if power_val is not None else None,
        "diversity_val": int(diversity_val),
        "within_duplicate_total": int(within_duplicate_total),
        "team_duplicate_total": int(team_duplicate_total),
        "roster": roster,
        "movesets": movesets,
        "coverage": coverage,
        "weakest": {"type": weakest_type, "total": weakest_val},
        "team_names": [e["name"] for e in team],
    }


@app.route("/api/predict", methods=["POST"])
def api_predict():
    data = request.get_json()
    version_group = data.get("version_group", DEFAULT_VERSION_GROUP)
    context = _get_dataset_context(version_group)
    generation = context["generation"]
    team_input = data.get("team", [])
    acc_exponent = float(data.get("acc_exponent", 2.0))

    if not team_input or len(team_input) > 6:
        return jsonify({"error": "Provide 1-6 team members"}), 400

    team = []
    for entry in team_input:
        name = entry.get("pokemon", "").lower()
        moves = [m.lower() for m in entry.get("moves", []) if m]
        poke = context["poke_by_name"].get(name)
        if not poke:
            return jsonify({"error": f"Pokemon '{name}' not found"}), 404
        if not moves:
            return jsonify({"error": f"No moves selected for {name}"}), 400
        team.append({"name": name, "moves": moves})

    pool = list(context["poke_by_name"].values())
    scores = compute_scores(pool, acc_exponent=acc_exponent, generation=generation)
    defending_types = types_for_generation(generation)

    z_val = min(
        max(
            max((scores.get((e["name"], mn, t), 0) for mn in e["moves"]), default=0)
            for e in team
        )
        for t in defending_types
    )

    power_val = sum(
        scores.get((e["name"], mn, t), 0)
        for t in defending_types
        for e in team
        for mn in e["moves"]
    )

    result = _build_result(
        team,
        pool,
        scores,
        z_val=z_val,
        power_val=power_val,
        generation=generation,
        version_group=version_group,
        label=context["dataset"]["label"],
    )
    result["status"] = "OK"
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
