"""Flask web interface for the Gen 3 Pokemon Team Optimizer."""

import logging
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

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
    no_4x_weakness = bool(data.get("no_4x_weakness", False))

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

    excluded_pokemon = {n.lower() for n in data.get("excluded_pokemon", [])}

    pool, scores = _get_pool_and_scores(
        params.no_legendaries, acc_exponent, speed_bonus, no_4x_weakness
    )

    if excluded_pokemon:
        pool = [p for p in pool if p["name"] not in excluded_pokemon]

    t1 = time.perf_counter()
    model = build_model(pool, scores, params)
    log.info("optimize: model build %.3fs", time.perf_counter() - t1)

    t2 = time.perf_counter()
    status, result, z_val, obj_val = solve_model(model)
    log.info("optimize: solve %.3fs", time.perf_counter() - t2)

    log.info("optimize: total %.3fs", time.perf_counter() - t0)
    if status != "Optimal":
        return jsonify({"status": status, "message": result})

    return jsonify(_build_result(result, pool, scores, z_val, obj_val))


def _gen3_damage(power, atk_base, move_type, poke_types, defender_base=100):
    """Gen 3 damage formula for a SE hit at Lv100, 0 IV / 0 EV."""
    A = 2 * atk_base + 5
    D = 2 * defender_base + 5
    base = (42 * power * A // D) // 50 + 2
    damage = int(base * (1.5 if move_type in poke_types else 1.0))
    return damage * 2


def _build_result(team, pool, scores, z_val, obj_val=None):
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
            atk_base = (
                p["base_stats"]["attack"]
                if m_type in PHYSICAL_TYPES
                else p["base_stats"]["special-attack"]
            )
            has_se = any(is_super_effective(m_type, t) for t in ALL_TYPES)
            best_se = (
                _gen3_damage(raw_power, atk_base, m_type, p["types"]) if has_se else 0
            )
            moves.append(
                {
                    "name": mname,
                    "type": m_type,
                    "power": raw_power // 2 if is_mt else raw_power,
                    "is_multi_turn": is_mt,
                    "accuracy": acc,
                    "category": "Physical" if m_type in PHYSICAL_TYPES else "Special",
                    "best_se": best_se,
                }
            )
        movesets.append({"pokemon": entry["name"], "moves": moves})

    coverage = []
    weakest_type, weakest_val = None, float("inf")
    for t in ALL_TYPES:
        row = {"type": t, "cells": []}
        row_total = 0
        for entry in team:
            p = poke_by_name[entry["name"]]
            best = 0
            for mn in entry["moves"]:
                md = move_by_name.get(mn, {})
                m_type = md.get("type")
                m_power = md.get("power", 0) or 0
                if not m_type or not is_super_effective(m_type, t):
                    continue
                atk_base = (
                    p["base_stats"]["attack"]
                    if m_type in PHYSICAL_TYPES
                    else p["base_stats"]["special-attack"]
                )
                dmg = _gen3_damage(m_power, atk_base, m_type, p["types"])
                if dmg > best:
                    best = dmg
            row["cells"].append(best)
            row_total += best
        row["total"] = row_total
        if row_total < weakest_val:
            weakest_val, weakest_type = row_total, t
        coverage.append(row)

    tiebreaker = (obj_val - z_val) if obj_val is not None else 0
    if obj_val and obj_val > 0:
        z_pct = z_val / obj_val * 100
        tb_pct = tiebreaker / obj_val * 100
    else:
        z_pct = 100.0
        tb_pct = 0.0

    return {
        "status": "Optimal",
        "z_val": round(z_val, 1),
        "obj_val": round(obj_val, 1) if obj_val is not None else None,
        "z_pct": round(z_pct, 2),
        "tiebreaker": round(tiebreaker, 1),
        "tb_pct": round(tb_pct, 2),
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

    result = _build_result(team, pool, scores, z_val=0)
    result["status"] = "OK"
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
