"""Flask web interface for the Gen 3 Pokemon Team Optimizer."""

import logging
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

from data.pokedex import FRLG_UNLIMITED_TMS
from optimiser.main import DATA_PATH, load_pokemon
from optimiser.scoring import ALL_TYPES, PHYSICAL_TYPES, compute_scores
from optimiser.solver import Params, build_model, solve_model

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
):
    key = (no_legendaries, acc_exponent, speed_bonus)
    if key not in _score_cache:
        pool = (
            [p for p in _all_pokemon if not p["is_legendary"]]
            if no_legendaries
            else _all_pokemon
        )
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


@app.route("/optimize", methods=["POST"])
def optimize():
    log.info("optimize: started")
    t0 = time.perf_counter()
    data = request.get_json()

    max_overlap = int(data.get("max_overlap", 1))
    min_redundancy = int(data.get("min_redundancy", 2))
    acc_exponent = float(data.get("acc_exponent", 2.0))
    duplicate_type_discount = float(data.get("duplicate_type_discount", 0.2))
    speed_bonus = float(data.get("speed_bonus", 0.25))
    allow_legendaries = bool(data.get("allow_legendaries", False))

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
        duplicate_type_discount=duplicate_type_discount,
        no_legendaries=not allow_legendaries,
        locked_pokemon=locked_pokemon,
        must_have_moves=[m.lower() for m in data.get("must_have_moves", [])],
        must_have_types=[t.lower() for t in data.get("must_have_types", [])],
        unlimited_tms=FRLG_UNLIMITED_TMS,
    )

    pool, scores = _get_pool_and_scores(
        params.no_legendaries, acc_exponent, speed_bonus
    )

    t1 = time.perf_counter()
    model = build_model(pool, scores, params)
    log.info("optimize: model build %.3fs", time.perf_counter() - t1)

    t2 = time.perf_counter()
    status, result, z_val = solve_model(model)
    log.info("optimize: solve %.3fs", time.perf_counter() - t2)

    log.info("optimize: total %.3fs", time.perf_counter() - t0)
    if status != "Optimal":
        return jsonify({"status": status, "message": result})

    return jsonify(_build_result(result, pool, scores, z_val))


def _build_result(team, pool, scores, z_val):
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
            is_mt = md.get("is_multi_turn", False)
            acc = md.get("accuracy") or 100
            best_se = max(
                (scores.get((p["name"], mname, t), 0) for t in ALL_TYPES), default=0
            )
            moves.append(
                {
                    "name": mname,
                    "type": m_type,
                    "power": raw_power // 2 if is_mt else raw_power,
                    "is_multi_turn": is_mt,
                    "accuracy": acc,
                    "category": "Physical" if m_type in PHYSICAL_TYPES else "Special",
                    "best_se": round(best_se),
                }
            )
        movesets.append({"pokemon": entry["name"], "moves": moves})

    coverage = []
    weakest_type, weakest_val = None, float("inf")
    for t in ALL_TYPES:
        row = {"type": t, "cells": []}
        row_total = 0
        for entry in team:
            best = max(
                (scores.get((entry["name"], mn, t), 0) for mn in entry["moves"]),
                default=0,
            )
            row["cells"].append(round(best))
            row_total += best
        row["total"] = round(row_total)
        if row_total < weakest_val:
            weakest_val, weakest_type = row_total, t
        coverage.append(row)

    return {
        "status": "Optimal",
        "min_coverage": round(z_val, 1),
        "roster": roster,
        "movesets": movesets,
        "coverage": coverage,
        "weakest": {"type": weakest_type, "total": round(weakest_val)},
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

    result = _build_result(team, pool, scores, z_val=0)
    result["min_coverage"] = result["weakest"]["total"]
    result["status"] = "OK"
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
