import pytest

from optimiser.scoring import (
    compute_scores,
    filter_dominated_moves,
    has_4x_weakness,
    move_category,
    move_penalty_factor,
    type_multiplier,
    types_for_generation,
)


def _move(name: str, move_type: str, power: int = 100, **overrides) -> dict:
    base = {
        "name": name,
        "type": move_type,
        "power": power,
        "accuracy": 100,
        "pp": 10,
        "damage_class": "physical",
        "learn_methods": ["level-up"],
        "level_learned_at": 1,
        "tm_only": False,
        "is_multi_turn": False,
        "recoil_pct": 0,
        "is_low_priority": False,
        "multi_hit": 1.0,
        "is_lock_in": False,
        "is_self_ko": False,
        "self_stat_changes": [],
    }
    base.update(overrides)
    return base


def test_compute_scores_uses_gen3_type_based_attack_split():
    pokemon_pool = [
        {
            "name": "mixedmon",
            "types": ["fire"],
            "base_stats": {
                "attack": 200,
                "special-attack": 50,
                "speed": 100,
            },
            "moves": [
                _move("fire-punch", "fire"),
                _move("brick-break", "fighting"),
            ],
        }
    ]

    scores = compute_scores(pokemon_pool, speed_bonus=0.0)

    # In Gen 3, Fire is special even for a move like Fire Punch.
    assert scores["mixedmon", "fire-punch", "grass"] == 15000.0
    assert scores["mixedmon", "brick-break", "normal"] == 40000.0


def test_compute_scores_uses_gen4_move_damage_class():
    pokemon_pool = [
        {
            "name": "mixedmon",
            "types": ["fire"],
            "base_stats": {
                "attack": 200,
                "special-attack": 50,
                "speed": 100,
            },
            "moves": [
                _move("fire-punch", "fire"),
            ],
        }
    ]

    scores = compute_scores(pokemon_pool, speed_bonus=0.0, generation=4)

    # In Gen 4, Fire Punch becomes physical because damage class is per move.
    assert scores["mixedmon", "fire-punch", "grass"] == 60000.0


def test_generation_8_supports_fairy_type_chart():
    assert "fairy" not in types_for_generation(4)
    assert "fairy" in types_for_generation(8)
    assert type_multiplier("steel", "fairy", generation=8) == 2.0
    assert type_multiplier("dragon", "fairy", generation=8) == 0.0


def test_move_penalty_factor_self_ko():
    move = _move("explosion", "normal", is_self_ko=True)
    assert move_penalty_factor(move) == 0.35


def test_move_penalty_factor_lock_in():
    move = _move("outrage", "dragon", is_lock_in=True)
    assert move_penalty_factor(move) == 0.8


def test_move_penalty_factor_self_stat_drop():
    move = _move(
        "overheat",
        "fire",
        self_stat_changes=[("special-attack", -2)],
    )
    assert move_penalty_factor(move) == 0.8


def test_move_penalty_factor_plain_move():
    move = _move("flamethrower", "fire")
    assert move_penalty_factor(move) == 1.0


def test_self_ko_discounts_score():
    pool = [
        {
            "name": "bomber",
            "types": ["normal"],
            "base_stats": {"attack": 100, "special-attack": 100, "speed": 100},
            "moves": [
                _move("explosion", "normal", is_self_ko=True),
                _move("body-slam", "normal"),
            ],
        }
    ]
    scores = compute_scores(pool, speed_bonus=0.0)
    assert (
        scores["bomber", "explosion", "normal"]
        < scores["bomber", "body-slam", "normal"]
    )


# ---------------------------------------------------------------------------
# Type chart: immunities
# ---------------------------------------------------------------------------


def test_legacy_immunities():
    assert type_multiplier("normal", "ghost", generation=3) == 0.0
    assert type_multiplier("electric", "ground", generation=3) == 0.0
    assert type_multiplier("fighting", "ghost", generation=3) == 0.0
    assert type_multiplier("ground", "flying", generation=3) == 0.0
    assert type_multiplier("psychic", "dark", generation=3) == 0.0
    assert type_multiplier("ghost", "normal", generation=3) == 0.0
    assert type_multiplier("poison", "steel", generation=3) == 0.0


def test_modern_dragon_fairy_immunity():
    assert type_multiplier("dragon", "fairy", generation=8) == 0.0
    assert type_multiplier("dragon", "fairy", generation=3) == 1.0


def test_ghost_steel_resistance_removed_in_gen6():
    assert type_multiplier("ghost", "steel", generation=3) == 0.5
    assert type_multiplier("ghost", "steel", generation=8) == 1.0


# ---------------------------------------------------------------------------
# move_category: generation-based physical/special split
# ---------------------------------------------------------------------------


def test_move_category_gen3_type_based():
    assert move_category({"type": "fire", "power": 60}, generation=3) == "special"
    assert move_category({"type": "fighting", "power": 60}, generation=3) == "physical"
    assert move_category({"type": "dark", "power": 60}, generation=3) == "special"
    assert move_category({"type": "ghost", "power": 60}, generation=3) == "physical"


def test_move_category_gen4_uses_damage_class():
    assert (
        move_category(
            {"type": "fire", "power": 75, "damage_class": "physical"}, generation=4
        )
        == "physical"
    )
    assert (
        move_category(
            {"type": "fighting", "power": 120, "damage_class": "special"}, generation=8
        )
        == "special"
    )


def test_move_category_status_returns_damage_class_all_gens():
    status = {"type": "normal", "power": 0, "damage_class": "status"}
    assert move_category(status, generation=3) == "status"
    assert move_category(status, generation=8) == "status"


def test_move_category_rejects_unsupported_generation():
    with pytest.raises(ValueError):
        move_category({"type": "fire", "power": 60}, generation=2)


# ---------------------------------------------------------------------------
# has_4x_weakness
# ---------------------------------------------------------------------------


def test_has_4x_weakness_ice_flying():
    assert has_4x_weakness(["grass", "flying"], generation=3) is True


def test_has_4x_weakness_single_type():
    assert has_4x_weakness(["fire"], generation=3) is False


def test_has_4x_weakness_no_overlap():
    assert has_4x_weakness(["water", "fire"], generation=3) is False


def test_has_4x_weakness_fairy_gen8():
    assert has_4x_weakness(["fighting", "dragon"], generation=8) is True
    assert has_4x_weakness(["fighting", "dragon"], generation=3) is False


# ---------------------------------------------------------------------------
# compute_scores: status/zero-power moves excluded
# ---------------------------------------------------------------------------


def test_compute_scores_skips_zero_power_moves():
    pool = [
        {
            "name": "supporter",
            "types": ["normal"],
            "base_stats": {"attack": 100, "special-attack": 100, "speed": 80},
            "moves": [
                _move("growl", "normal", power=0, damage_class="status"),
                _move("tackle", "normal", power=40),
            ],
        }
    ]
    scores = compute_scores(pool, speed_bonus=0.0)
    assert not any(m == "growl" for (_, m, _) in scores)
    assert any(m == "tackle" for (_, m, _) in scores)


# ---------------------------------------------------------------------------
# compute_scores: fairy STAB and SE in gen 8
# ---------------------------------------------------------------------------


def test_compute_scores_fairy_stab_and_se_in_gen8():
    pool = [
        {
            "name": "fairy-mon",
            "types": ["fairy"],
            "base_stats": {"attack": 100, "special-attack": 100, "speed": 80},
            "moves": [_move("moonblast", "fairy", damage_class="special")],
        }
    ]
    scores = compute_scores(pool, speed_bonus=0.0, generation=8)

    assert ("fairy-mon", "moonblast", "dragon") in scores
    assert ("fairy-mon", "moonblast", "dark") in scores
    assert ("fairy-mon", "moonblast", "fighting") in scores
    assert (
        scores["fairy-mon", "moonblast", "dragon"]
        > scores["fairy-mon", "moonblast", "fairy"]
    )


def test_compute_scores_fairy_not_in_gen3():
    pool = [
        {
            "name": "fairy-mon",
            "types": ["fairy"],
            "base_stats": {"attack": 100, "special-attack": 100, "speed": 80},
            "moves": [_move("moonblast", "fairy", damage_class="special")],
        }
    ]
    scores = compute_scores(pool, speed_bonus=0.0, generation=3)
    assert not any(dt == "fairy" for (_, _, dt) in scores)


# ---------------------------------------------------------------------------
# filter_dominated_moves
# ---------------------------------------------------------------------------


def test_filter_dominated_moves_prunes_weak_same_type():
    pool = [
        {
            "name": "attacker",
            "types": ["fire"],
            "base_stats": {"attack": 100, "special-attack": 100, "speed": 100},
            "moves": [
                _move("ember", "fire", power=40),
                _move("flamethrower", "fire", power=90),
            ],
        }
    ]
    filtered = filter_dominated_moves(pool)
    move_names = {m["name"] for m in filtered[0]["moves"]}
    assert "flamethrower" in move_names
    assert "ember" not in move_names


def test_filter_dominated_moves_keeps_tm_moves():
    pool = [
        {
            "name": "attacker",
            "types": ["fire"],
            "base_stats": {"attack": 100, "special-attack": 100, "speed": 100},
            "moves": [
                _move("flamethrower", "fire", power=90),
                _move(
                    "fire-blast",
                    "fire",
                    power=110,
                    tm_only=True,
                    learn_methods=["machine"],
                ),
            ],
        }
    ]
    filtered = filter_dominated_moves(pool)
    move_names = {m["name"] for m in filtered[0]["moves"]}
    assert "fire-blast" in move_names
    assert "flamethrower" in move_names


def test_filter_dominated_moves_keeps_protected_moves():
    pool = [
        {
            "name": "attacker",
            "types": ["normal"],
            "base_stats": {"attack": 100, "special-attack": 100, "speed": 100},
            "moves": [
                _move("tackle", "normal", power=40),
                _move("body-slam", "normal", power=85),
            ],
        }
    ]
    filtered = filter_dominated_moves(
        pool, protected_moves_by_pokemon={"attacker": {"tackle"}}
    )
    move_names = {m["name"] for m in filtered[0]["moves"]}
    assert "tackle" in move_names


def test_filter_dominated_moves_keeps_different_types():
    pool = [
        {
            "name": "attacker",
            "types": ["normal"],
            "base_stats": {"attack": 100, "special-attack": 100, "speed": 100},
            "moves": [
                _move("body-slam", "normal", power=85),
                _move("ember", "fire", power=40),
            ],
        }
    ]
    filtered = filter_dominated_moves(pool)
    move_names = {m["name"] for m in filtered[0]["moves"]}
    assert "body-slam" in move_names
    assert "ember" in move_names
