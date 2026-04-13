from optimiser.solver import Params, _build_index, build_model, solve_model


def _move(name: str, move_type: str, *, tm_only: bool = False) -> dict:
    return {
        "name": name,
        "type": move_type,
        "power": 80,
        "accuracy": 100,
        "pp": 15,
        "damage_class": "physical",
        "learn_methods": ["machine"] if tm_only else ["level-up"],
        "level_learned_at": 1,
        "tm_only": tm_only,
        "is_multi_turn": False,
        "recoil_pct": 0,
        "is_low_priority": False,
        "multi_hit": 1.0,
        "is_lock_in": False,
        "is_self_ko": False,
        "self_stat_changes": [],
    }


def _pokemon(name: str, moves: list[dict]) -> dict:
    return {
        "name": name,
        "types": ["normal"],
        "base_stats": {
            "attack": 100,
            "special-attack": 100,
            "speed": 100,
        },
        "moves": moves,
    }


def _typed_pokemon(name: str, types: list[str], moves: list[dict]) -> dict:
    return {
        "name": name,
        "types": types,
        "base_stats": {
            "attack": 100,
            "special-attack": 100,
            "speed": 100,
        },
        "moves": moves,
    }


def test_build_index_tracks_single_use_tm_users():
    pokemon_pool = [
        _pokemon("alpha", [_move("shared-tm", "normal", tm_only=True)]),
        _pokemon("beta", [_move("shared-tm", "normal", tm_only=True)]),
    ]

    _, _, _, _, single_use_tm_users = _build_index(
        pokemon_pool,
        scores={},
        unlimited_tms=set(),
    )
    assert single_use_tm_users == {"shared-tm": ["alpha", "beta"]}

    _, _, _, _, no_single_use_tm_users = _build_index(
        pokemon_pool,
        scores={},
        unlimited_tms={"shared-tm"},
    )
    assert no_single_use_tm_users == {}


def test_solve_model_selects_six_pokemon_and_four_moves_each():
    pokemon_pool = []
    scores = {}

    for idx in range(6):
        name = f"poke-{idx}"
        moves = []
        for move_idx, move_type in enumerate(
            ["normal", "fire", "water", "grass"], start=1
        ):
            move_name = f"{name}-move-{move_idx}"
            moves.append(_move(move_name, move_type))
            for def_type in ["normal", "fire", "water", "grass"]:
                scores[name, move_name, def_type] = float(10 + move_idx)
        pokemon_pool.append(_pokemon(name, moves))

    params = Params(
        max_overlap=6,
        min_redundancy=0,
        max_same_type_moves=4,
        min_role_types=0,
    )

    model = build_model(pokemon_pool, scores, params)
    status, team, _, _ = solve_model(model)

    assert status == "Optimal"
    assert len(team) == 6
    assert {entry["name"] for entry in team} == {p["name"] for p in pokemon_pool}
    assert all(len(entry["moves"]) == 4 for entry in team)


def test_solve_model_honors_must_have_fairy_type_when_feasible():
    pokemon_pool = []
    scores = {}

    for idx in range(7):
        name = f"poke-{idx}"
        types = ["fairy"] if idx == 6 else ["normal"]
        moves = []
        for move_idx, move_type in enumerate(
            ["normal", "fire", "water", "grass"], start=1
        ):
            move_name = f"{name}-move-{move_idx}"
            moves.append(_move(move_name, move_type))
            for def_type in ["normal", "fire", "water", "grass", "fairy"]:
                scores[name, move_name, def_type] = float(10 + move_idx)
        pokemon_pool.append(_typed_pokemon(name, types, moves))

    params = Params(
        max_overlap=6,
        min_redundancy=0,
        max_same_type_moves=4,
        min_role_types=0,
        must_have_types=["fairy"],
    )

    model = build_model(pokemon_pool, scores, params, generation=8)
    status, team, _, _ = solve_model(model)

    assert status == "Optimal"
    assert any(entry["name"] == "poke-6" for entry in team)


def test_solve_model_is_infeasible_when_must_have_type_missing():
    pokemon_pool = []
    scores = {}

    for idx in range(6):
        name = f"poke-{idx}"
        moves = []
        for move_idx, move_type in enumerate(
            ["normal", "fire", "water", "grass"], start=1
        ):
            move_name = f"{name}-move-{move_idx}"
            moves.append(_move(move_name, move_type))
            for def_type in ["normal", "fire", "water", "grass", "fairy"]:
                scores[name, move_name, def_type] = float(10 + move_idx)
        pokemon_pool.append(_pokemon(name, moves))

    params = Params(
        max_overlap=6,
        min_redundancy=0,
        max_same_type_moves=4,
        min_role_types=0,
        must_have_types=["fairy"],
    )

    model = build_model(pokemon_pool, scores, params, generation=8)
    status, _, _, _ = solve_model(model)

    assert status == "Infeasible"


def test_solve_model_is_infeasible_when_must_have_move_missing():
    pokemon_pool = []
    scores = {}

    for idx in range(6):
        name = f"poke-{idx}"
        moves = []
        for move_idx, move_type in enumerate(
            ["normal", "fire", "water", "grass"], start=1
        ):
            move_name = f"{name}-move-{move_idx}"
            moves.append(_move(move_name, move_type))
            for def_type in ["normal", "fire", "water", "grass"]:
                scores[name, move_name, def_type] = float(10 + move_idx)
        pokemon_pool.append(_pokemon(name, moves))

    params = Params(
        max_overlap=6,
        min_redundancy=0,
        max_same_type_moves=4,
        min_role_types=0,
        must_have_moves=["missing-move"],
    )

    model = build_model(pokemon_pool, scores, params)
    status, _, _, _ = solve_model(model)

    assert status == "Infeasible"


def test_solve_model_honors_must_include_any_of_pokemon_when_feasible():
    pokemon_pool = []
    scores = {}

    for idx in range(7):
        name = f"poke-{idx}"
        moves = []
        for move_idx, move_type in enumerate(
            ["normal", "fire", "water", "grass"], start=1
        ):
            move_name = f"{name}-move-{move_idx}"
            moves.append(_move(move_name, move_type))
            for def_type in ["normal", "fire", "water", "grass"]:
                scores[name, move_name, def_type] = float(10 + move_idx)
        pokemon_pool.append(_pokemon(name, moves))

    params = Params(
        max_overlap=6,
        min_redundancy=0,
        max_same_type_moves=4,
        min_role_types=0,
        must_include_any_of_pokemon=["poke-6", "starter-b"],
    )

    model = build_model(pokemon_pool, scores, params)
    status, team, _, _ = solve_model(model)

    assert status == "Optimal"
    assert any(entry["name"] == "poke-6" for entry in team)


def test_solve_model_is_infeasible_when_required_any_of_pokemon_missing():
    pokemon_pool = []
    scores = {}

    for idx in range(6):
        name = f"poke-{idx}"
        moves = []
        for move_idx, move_type in enumerate(
            ["normal", "fire", "water", "grass"], start=1
        ):
            move_name = f"{name}-move-{move_idx}"
            moves.append(_move(move_name, move_type))
            for def_type in ["normal", "fire", "water", "grass"]:
                scores[name, move_name, def_type] = float(10 + move_idx)
        pokemon_pool.append(_pokemon(name, moves))

    params = Params(
        max_overlap=6,
        min_redundancy=0,
        max_same_type_moves=4,
        min_role_types=0,
        must_include_any_of_pokemon=["starter-a", "starter-b"],
    )

    model = build_model(pokemon_pool, scores, params)
    status, _, _, _ = solve_model(model)

    assert status == "Infeasible"


def test_solve_model_enforces_single_use_tm():
    """Two pokemon share a TM move; solver must give it to at most one."""
    shared_tm = _move("ice-beam", "ice", tm_only=True)
    pokemon_pool = []
    scores = {}

    for idx in range(6):
        name = f"poke-{idx}"
        moves = [shared_tm] if idx < 2 else []
        for move_idx, move_type in enumerate(
            ["normal", "fire", "water", "grass"], start=1
        ):
            move_name = f"{name}-m{move_idx}"
            moves.append(_move(move_name, move_type))
            for def_type in ["normal", "fire", "water", "grass", "ice"]:
                scores[name, move_name, def_type] = float(10 + move_idx)

        if idx < 2:
            for def_type in ["normal", "fire", "water", "grass", "ice"]:
                scores[name, "ice-beam", def_type] = 50.0

        pokemon_pool.append(_pokemon(name, moves))

    params = Params(
        max_overlap=6,
        min_redundancy=0,
        max_same_type_moves=4,
        min_role_types=0,
        unlimited_tms=set(),
    )

    model = build_model(pokemon_pool, scores, params)
    status, team, _, _ = solve_model(model)

    assert status == "Optimal"
    users_with_tm = [e for e in team if "ice-beam" in e["moves"]]
    assert len(users_with_tm) <= 1


def test_solve_model_unlimited_tm_allows_sharing():
    """When a TM is unlimited, multiple pokemon may carry it."""
    shared_tm = _move("ice-beam", "ice", tm_only=True)
    pokemon_pool = []
    scores = {}

    for idx in range(6):
        name = f"poke-{idx}"
        moves = [shared_tm] if idx < 2 else []
        for move_idx, move_type in enumerate(
            ["normal", "fire", "water", "grass"], start=1
        ):
            move_name = f"{name}-m{move_idx}"
            moves.append(_move(move_name, move_type))
            for def_type in ["normal", "fire", "water", "grass", "ice"]:
                scores[name, move_name, def_type] = float(10 + move_idx)

        if idx < 2:
            for def_type in ["normal", "fire", "water", "grass", "ice"]:
                scores[name, "ice-beam", def_type] = 500.0

        pokemon_pool.append(_pokemon(name, moves))

    params = Params(
        max_overlap=6,
        min_redundancy=0,
        max_same_type_moves=4,
        min_role_types=0,
        unlimited_tms={"ice-beam"},
    )

    model = build_model(pokemon_pool, scores, params)
    status, team, _, _ = solve_model(model)

    assert status == "Optimal"
    users_with_tm = [e for e in team if "ice-beam" in e["moves"]]
    assert len(users_with_tm) == 2


def test_solve_model_respects_max_same_type_moves():
    """With max_same_type_moves=1, no pokemon should carry two moves of the same type."""
    pokemon_pool = []
    scores = {}

    for idx in range(6):
        name = f"poke-{idx}"
        moves = [
            _move(f"{name}-fire1", "fire"),
            _move(f"{name}-fire2", "fire"),
            _move(f"{name}-water", "water"),
            _move(f"{name}-grass", "grass"),
            _move(f"{name}-normal", "normal"),
        ]
        for m in moves:
            for def_type in ["normal", "fire", "water", "grass"]:
                scores[name, m["name"], def_type] = 100.0
        pokemon_pool.append(_pokemon(name, moves))

    params = Params(
        max_overlap=6,
        min_redundancy=0,
        max_same_type_moves=1,
        min_role_types=0,
    )

    model = build_model(pokemon_pool, scores, params)
    status, team, _, _ = solve_model(model)

    assert status == "Optimal"
    move_type_lookup = {m["name"]: m["type"] for p in pokemon_pool for m in p["moves"]}
    for entry in team:
        from collections import Counter

        counts = Counter(move_type_lookup[mn] for mn in entry["moves"])
        assert all(c <= 1 for c in counts.values())
