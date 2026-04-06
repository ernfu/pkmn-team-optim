"""Flask web interface for the Gen 3 Pokemon Team Optimizer."""

from collections import defaultdict
import json
import logging
import math
import queue
import threading
import time

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

from data.pokedex import FRLG_UNLIMITED_TMS
from optimiser.main import DATA_PATH, load_pokemon
from optimiser.scoring import (
    ALL_TYPES,
    PHYSICAL_TYPES,
    compute_scores,
    has_4x_weakness,
    is_super_effective,
)
from optimiser.solver import Params, _diagnose_infeasibility, build_model, solve_model

app = Flask(__name__)

_all_pokemon = load_pokemon(DATA_PATH, no_legendaries=False)
_poke_by_name = {p["name"]: p for p in _all_pokemon}

_move_lookup: dict[str, dict] = {}
for _p in _all_pokemon:
    for _m in _p["moves"]:
        if _m["name"] not in _move_lookup:
            _move_lookup[_m["name"]] = _m

_all_moves_sorted = sorted(
    n for n, m in _move_lookup.items() if m.get("power") and m["power"] > 0
)

_score_cache: dict[tuple, tuple] = {}


def _get_pool_and_scores(
    no_legendaries: bool,
    acc_exponent: float,
    speed_bonus: float = 0.25,
    no_4x_weakness: bool = False,
):
    key = (no_legendaries, acc_exponent, speed_bonus, no_4x_weakness)
    if key not in _score_cache:
        pool = (
            [p for p in _all_pokemon if not p["is_legendary"]]
            if no_legendaries
            else list(_all_pokemon)
        )
        if no_4x_weakness:
            pool = [p for p in pool if not has_4x_weakness(p["types"])]
        scores = compute_scores(
            pool,
            acc_exponent=acc_exponent,
            speed_bonus=speed_bonus,
        )
        _score_cache[key] = (pool, scores)
    return _score_cache[key]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/pokemon")
def api_pokemon():
    allow_leg = request.args.get("allow_legendaries", "false").lower() == "true"
    pool = (
        _all_pokemon
        if allow_leg
        else [p for p in _all_pokemon if not p["is_legendary"]]
    )
    return jsonify(sorted(p["name"] for p in pool))


@app.route("/api/moves/<pokemon>")
def api_moves(pokemon):
    poke = _poke_by_name.get(pokemon.lower())
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
    return jsonify(_all_moves_sorted)


def _sse(event, data):
    """Format a single Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.route("/optimize-stream", methods=["POST"])
def optimize_stream():
    """SSE endpoint that streams solver progress then the final result."""
    data = request.get_json()

    max_overlap = int(data.get("max_overlap", 1))
    min_redundancy = int(data.get("min_redundancy", 2))
    max_same_type_moves = int(data.get("max_same_type_moves", 2))
    acc_exponent = float(data.get("acc_exponent", 2.0))
    speed_bonus = float(data.get("speed_bonus", 0.25))
    allow_legendaries = bool(data.get("allow_legendaries", False))
    no_4x_weakness = bool(data.get("no_4x_weakness", False))
    exact_solve = bool(data.get("exact_solve", False))

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
        no_legendaries=not allow_legendaries,
        locked_pokemon=locked_pokemon,
        must_have_moves=[m.lower() for m in data.get("must_have_moves", [])],
        must_have_types=[t.lower() for t in data.get("must_have_types", [])],
        unlimited_tms=FRLG_UNLIMITED_TMS,
    )

    excluded_pokemon = {n.lower() for n in data.get("excluded_pokemon", [])}

    pool, scores = _get_pool_and_scores(
        params.no_legendaries, acc_exponent, speed_bonus, no_4x_weakness
    )

    if excluded_pokemon:
        pool = [p for p in pool if p["name"] not in excluded_pokemon]

    time_limit = 600 if exact_solve else 120

    def generate():
        yield _sse("phase", {"phase": "building"})

        model = build_model(pool, scores, params)
        yield _sse("phase", {"phase": "solving"})

        progress_q = queue.Queue()
        result_holder = [None]
        total_time_limit = time_limit * 3

        def _run_solver():
            def _on_progress(stage, gap, nodes, elapsed):
                if gap is None and nodes is None:
                    progress_q.put(("phase", stage, round(elapsed, 2)))
                else:
                    progress_q.put(("progress", stage, gap, int(nodes), round(elapsed, 2)))

            result_holder[0] = solve_model(
                model, progress_fn=_on_progress, exact_solve=exact_solve
            )
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
            _, diag_msg, _ = _diagnose_infeasibility(params)
            yield _sse("error", {"status": status, "message": diag_msg})
        else:
            yield _sse("result", _build_result(result, pool, scores, z_val, power_val))

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _gen3_damage(power, atk_base, move_type, poke_types, defender_base=100):
    """Gen 3 damage formula for a SE hit at Lv100, 0 IV / 0 EV."""
    A = 2 * atk_base + 5
    D = 2 * defender_base + 5
    base = (42 * power * A // D) // 50 + 2
    damage = int(base * (1.5 if move_type in poke_types else 1.0))
    return damage * 2


def _build_result(team, pool, scores, z_val, power_val=None):
    poke_by_name = {p["name"]: p for p in pool}
    move_by_name: dict[str, dict] = {}
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
            atk_base = (
                p["base_stats"]["attack"]
                if m_type in PHYSICAL_TYPES
                else p["base_stats"]["special-attack"]
            )
            has_se = any(is_super_effective(m_type, t) for t in ALL_TYPES)
            best_se = (
                _gen3_damage(effective_power, atk_base, m_type, p["types"])
                if has_se
                else 0
            )
            moves.append(
                {
                    "name": mname,
                    "type": m_type,
                    "power": effective_power / 2 if is_mt else effective_power,
                    "is_multi_turn": is_mt,
                    "accuracy": acc,
                    "category": "Physical" if m_type in PHYSICAL_TYPES else "Special",
                    "best_se": best_se,
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
        within_duplicate_total += sum(max(count - 1, 0) for count in poke_type_counts.values())

    team_duplicate_total = sum(max(count - 1, 0) for count in team_type_counts.values())
    diversity_val = within_duplicate_total + team_duplicate_total

    if power_val is None:
        power_val = sum(
            scores.get((entry["name"], mname, t), 0)
            for t in ALL_TYPES
            for entry in team
            for mname in entry["moves"]
        )

    coverage = []
    weakest_type, weakest_val = None, float("inf")
    for t in ALL_TYPES:
        row = {"type": t, "cells": []}
        for entry in team:
            p = poke_by_name[entry["name"]]
            best = 0
            for mn in entry["moves"]:
                md = move_by_name.get(mn, {})
                m_type = md.get("type")
                m_power = md.get("power", 0) or 0
                m_multi = md.get("multi_hit", 1.0)
                if not m_type or not is_super_effective(m_type, t):
                    continue
                atk_base = (
                    p["base_stats"]["attack"]
                    if m_type in PHYSICAL_TYPES
                    else p["base_stats"]["special-attack"]
                )
                dmg = _gen3_damage(m_power * m_multi, atk_base, m_type, p["types"])
                if dmg > best:
                    best = dmg
            row["cells"].append(best)
        row["total"] = max(row["cells"]) if row["cells"] else 0
        if row["total"] < weakest_val:
            weakest_val, weakest_type = row["total"], t
        coverage.append(row)

    return {
        "status": "Optimal",
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
    team_input = data.get("team", [])
    acc_exponent = float(data.get("acc_exponent", 2.0))

    if not team_input or len(team_input) > 6:
        return jsonify({"error": "Provide 1-6 team members"}), 400

    team = []
    for entry in team_input:
        name = entry.get("pokemon", "").lower()
        moves = [m.lower() for m in entry.get("moves", []) if m]
        poke = _poke_by_name.get(name)
        if not poke:
            return jsonify({"error": f"Pokemon '{name}' not found"}), 404
        if not moves:
            return jsonify({"error": f"No moves selected for {name}"}), 400
        team.append({"name": name, "moves": moves})

    pool = list(_poke_by_name.values())
    scores = compute_scores(pool, acc_exponent=acc_exponent)

    z_val = min(
        max(
            max((scores.get((e["name"], mn, t), 0) for mn in e["moves"]), default=0)
            for e in team
        )
        for t in ALL_TYPES
    )

    power_val = sum(
        scores.get((e["name"], mn, t), 0)
        for t in ALL_TYPES
        for e in team
        for mn in e["moves"]
    )

    result = _build_result(team, pool, scores, z_val=z_val, power_val=power_val)
    result["status"] = "OK"
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
