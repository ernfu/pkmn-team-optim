import json

import pytest

from data import pokedex

# ---------------------------------------------------------------------------
# Generation name / version-group resolution helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("generation-i", 1),
        ("generation-ii", 2),
        ("generation-iii", 3),
        ("generation-iv", 4),
        ("generation-v", 5),
        ("generation-vi", 6),
        ("generation-vii", 7),
        ("generation-viii", 8),
        ("generation-ix", 9),
    ],
)
def test_parse_generation_name(name, expected):
    assert pokedex._parse_generation_name(name) == expected


# ---------------------------------------------------------------------------
# _resolve_pokemon_types
# ---------------------------------------------------------------------------


def test_resolve_pokemon_types_uses_past_for_old_gen():
    detail = {
        "types": ["fairy"],
        "past_types": [
            {"generation": "generation-v", "types": ["normal"]},
        ],
    }
    assert pokedex._resolve_pokemon_types(detail, generation=3) == ["normal"]
    assert pokedex._resolve_pokemon_types(detail, generation=5) == ["normal"]


def test_resolve_pokemon_types_uses_current_for_new_gen():
    detail = {
        "types": ["fairy"],
        "past_types": [
            {"generation": "generation-v", "types": ["normal"]},
        ],
    }
    assert pokedex._resolve_pokemon_types(detail, generation=6) == ["fairy"]
    assert pokedex._resolve_pokemon_types(detail, generation=8) == ["fairy"]


def test_resolve_pokemon_types_no_past_types():
    detail = {"types": ["electric"], "past_types": []}
    assert pokedex._resolve_pokemon_types(detail, generation=3) == ["electric"]


def test_resolve_pokemon_types_missing_key():
    detail = {"types": ["water"]}
    assert pokedex._resolve_pokemon_types(detail, generation=3) == ["water"]


def test_resolve_pokemon_types_multiple_past_entries():
    detail = {
        "types": ["electric", "steel"],
        "past_types": [
            {"generation": "generation-i", "types": ["electric"]},
        ],
    }
    assert pokedex._resolve_pokemon_types(detail, generation=1) == ["electric"]
    assert pokedex._resolve_pokemon_types(detail, generation=2) == ["electric", "steel"]


# ---------------------------------------------------------------------------
# _resolve_move_for_generation
# ---------------------------------------------------------------------------


def test_resolve_move_power_for_old_gen(monkeypatch):
    monkeypatch.setattr(
        pokedex,
        "_generation_for_version_group",
        {"black-white": 5}.get,
    )
    move = {
        "name": "tackle",
        "type": "normal",
        "power": 40,
        "accuracy": 100,
        "past_values": [
            {"power": 35, "accuracy": 95, "type": None, "version_group": "black-white"},
        ],
    }
    resolved = pokedex._resolve_move_for_generation(move, generation=3)
    assert resolved["power"] == 35
    assert resolved["accuracy"] == 95
    assert resolved["type"] == "normal"
    assert "past_values" not in resolved


def test_resolve_move_type_for_old_gen(monkeypatch):
    monkeypatch.setattr(
        pokedex,
        "_generation_for_version_group",
        {"x-y": 6}.get,
    )
    move = {
        "name": "charm",
        "type": "fairy",
        "power": None,
        "accuracy": 100,
        "past_values": [
            {"power": None, "accuracy": None, "type": "normal", "version_group": "x-y"},
        ],
    }
    resolved = pokedex._resolve_move_for_generation(move, generation=3)
    assert resolved["type"] == "normal"
    assert resolved["power"] is None
    assert resolved["accuracy"] == 100


def test_resolve_move_uses_current_for_new_gen(monkeypatch):
    monkeypatch.setattr(
        pokedex,
        "_generation_for_version_group",
        {"black-white": 5}.get,
    )
    move = {
        "name": "tackle",
        "type": "normal",
        "power": 40,
        "accuracy": 100,
        "past_values": [
            {"power": 35, "accuracy": 95, "type": None, "version_group": "black-white"},
        ],
    }
    resolved = pokedex._resolve_move_for_generation(move, generation=8)
    assert resolved["power"] == 40
    assert resolved["accuracy"] == 100


def test_resolve_move_multiple_past_entries(monkeypatch):
    monkeypatch.setattr(
        pokedex,
        "_generation_for_version_group",
        {"black-white": 5, "sun-moon": 7}.get,
    )
    move = {
        "name": "tackle",
        "type": "normal",
        "power": 40,
        "accuracy": 100,
        "past_values": [
            {"power": 35, "accuracy": 95, "type": None, "version_group": "black-white"},
            {"power": 50, "accuracy": None, "type": None, "version_group": "sun-moon"},
        ],
    }
    assert pokedex._resolve_move_for_generation(move, generation=3)["power"] == 35
    assert pokedex._resolve_move_for_generation(move, generation=6)["power"] == 50
    assert pokedex._resolve_move_for_generation(move, generation=8)["power"] == 40


def test_resolve_move_no_past_values():
    move = {"name": "surf", "type": "water", "power": 90, "accuracy": 100}
    resolved = pokedex._resolve_move_for_generation(move, generation=3)
    assert resolved["power"] == 90
    assert resolved["type"] == "water"


# ---------------------------------------------------------------------------
# _build_move_record
# ---------------------------------------------------------------------------


def test_build_move_record_applies_variable_power_estimate(monkeypatch):
    monkeypatch.setattr(
        pokedex,
        "_generation_for_version_group",
        lambda _: 6,
    )
    profile = {"variable_power_estimates": {"return": 102}}
    move_detail = {
        "name": "return",
        "type": "normal",
        "power": None,
        "accuracy": 100,
        "pp": 20,
        "damage_class": "physical",
        "past_values": [],
    }
    raw_move = {"name": "return", "learn_methods": ["machine"], "level_learned_at": 0}
    result = pokedex._build_move_record(
        "return", move_detail, raw_move, profile, generation=3
    )
    assert result["power"] == 102
    assert result["tm_only"] is True


def test_build_move_record_recoil_and_multi_hit(monkeypatch):
    monkeypatch.setattr(
        pokedex,
        "_generation_for_version_group",
        lambda _: 6,
    )
    profile = {"variable_power_estimates": {}}
    move_detail = {
        "name": "double-edge",
        "type": "normal",
        "power": 120,
        "accuracy": 100,
        "pp": 15,
        "damage_class": "physical",
        "drain": -33,
        "min_hits": None,
        "max_hits": None,
        "meta_category": "damage",
        "stat_chance": 0,
        "stat_changes": [],
        "priority": 0,
        "effect_text": "",
        "past_values": [],
    }
    raw_move = {
        "name": "double-edge",
        "learn_methods": ["level-up"],
        "level_learned_at": 38,
    }
    result = pokedex._build_move_record(
        "double-edge", move_detail, raw_move, profile, generation=3
    )
    assert result["recoil_pct"] == pytest.approx(0.33)
    assert result["multi_hit"] == 1.0
    assert result["is_self_ko"] is False
    assert result["tm_only"] is False


def test_build_move_record_self_ko_flag(monkeypatch):
    monkeypatch.setattr(
        pokedex,
        "_generation_for_version_group",
        lambda _: 6,
    )
    profile = {"variable_power_estimates": {}}
    move_detail = {
        "name": "explosion",
        "type": "normal",
        "power": 250,
        "accuracy": 100,
        "pp": 5,
        "damage_class": "physical",
        "drain": 0,
        "min_hits": None,
        "max_hits": None,
        "meta_category": "damage",
        "stat_chance": 0,
        "stat_changes": [],
        "priority": 0,
        "effect_text": "User faints.",
        "past_values": [],
    }
    raw_move = {
        "name": "explosion",
        "learn_methods": ["level-up"],
        "level_learned_at": 55,
    }
    result = pokedex._build_move_record(
        "explosion", move_detail, raw_move, profile, generation=3
    )
    assert result["is_self_ko"] is True


# ---------------------------------------------------------------------------
# Profile shape
# ---------------------------------------------------------------------------


def test_get_profile_merges_generation_rules():
    gen3_profile = pokedex.get_profile("firered-leafgreen-regional")
    gen3_national_profile = pokedex.get_profile("firered-leafgreen-national")
    gen4_profile = pokedex.get_profile("brilliant-diamond-shining-pearl-regional")
    gen4_national_profile = pokedex.get_profile(
        "brilliant-diamond-shining-pearl-national"
    )

    assert gen3_profile["generation"] == 3
    assert gen3_profile["variable_power_estimates"]["return"] == 102
    assert gen3_national_profile["pokedex"] == "national"
    assert gen3_national_profile["max_dex_number"] == 386
    assert gen3_national_profile["source_version_group"] == "firered-leafgreen-regional"
    assert gen3_national_profile["label"] == "FireRed / LeafGreen (National Dex)"

    assert gen4_profile["generation"] == 8
    assert gen4_profile["variable_power_estimates"]["return"] == 102
    assert gen4_profile["label"] == "Brilliant Diamond / Shining Pearl (Regional Dex)"
    assert gen4_national_profile["pokedex"] == "national"
    assert gen4_national_profile["max_dex_number"] == 493
    assert (
        gen4_national_profile["source_version_group"]
        == "brilliant-diamond-shining-pearl-regional"
    )
    assert (
        gen4_national_profile["label"]
        == "Brilliant Diamond / Shining Pearl (National Dex)"
    )


def test_compile_version_group_applies_current_gen3_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(pokedex, "COMPILED_DIR", tmp_path)
    monkeypatch.setattr(
        pokedex, "time", type("FakeTime", (), {"sleep": staticmethod(lambda _: None)})
    )
    monkeypatch.setattr(
        pokedex,
        "_merge_pre_evolution_moves",
        lambda compiled_pokemon, pre_evo_map=None: None,
    )
    monkeypatch.setattr(
        pokedex,
        "_generation_for_version_group",
        {"x-y": 6, "black-white": 5}.get,
    )

    monkeypatch.setattr(
        pokedex,
        "get_pokemon_list",
        lambda version_group: [
            {
                "entry_number": 35,
                "name": "clefairy",
                "species_url": "https://pokeapi.co/api/v2/pokemon-species/35/",
            },
            {
                "entry_number": 36,
                "name": "clefable",
                "species_url": "https://pokeapi.co/api/v2/pokemon-species/36/",
            },
        ],
    )
    monkeypatch.setattr(
        pokedex,
        "get_pokemon_detail",
        lambda name: {
            "id": 35 if name == "clefairy" else 36,
            "name": name,
            "types": ["fairy"],
            "past_types": [
                {"generation": "generation-v", "types": ["normal"]},
            ],
            "base_stats": {
                "hp": 70,
                "attack": 45,
                "defense": 48,
                "special-attack": 60,
                "special-defense": 65,
                "speed": 35,
            },
            "abilities": ["cute-charm"],
        },
    )
    monkeypatch.setattr(
        pokedex,
        "get_pokemon_moves",
        lambda name, version_group: [
            {
                "name": "charm",
                "learn_methods": ["level-up"],
                "level_learned_at": 1,
            },
            {
                "name": "tackle",
                "learn_methods": ["level-up"],
                "level_learned_at": 1,
            },
        ],
    )
    monkeypatch.setattr(
        pokedex,
        "get_species_detail",
        lambda name: {
            "name": name,
            "is_legendary": False,
            "evolves_from_species": "cleffa" if name == "clefairy" else "clefairy",
            "default_pokemon_name": name,
        },
    )

    move_details = {
        "charm": {
            "name": "charm",
            "type": "fairy",
            "power": None,
            "accuracy": 100,
            "pp": 20,
            "damage_class": "status",
            "past_values": [
                {
                    "power": None,
                    "accuracy": None,
                    "type": "normal",
                    "version_group": "x-y",
                },
            ],
        },
        "tackle": {
            "name": "tackle",
            "type": "normal",
            "power": 40,
            "accuracy": 100,
            "pp": 35,
            "damage_class": "physical",
            "past_values": [
                {
                    "power": 35,
                    "accuracy": 95,
                    "type": None,
                    "version_group": "black-white",
                },
            ],
        },
    }
    monkeypatch.setattr(
        pokedex, "get_move_detail", lambda name: dict(move_details[name])
    )

    out_path = pokedex.compile_version_group("firered-leafgreen-regional")
    compiled = json.loads(out_path.read_text())
    clefairy = next(p for p in compiled["pokemon"] if p["name"] == "clefairy")
    moves = {move["name"]: move for move in clefairy["moves"]}

    assert out_path == tmp_path / "firered-leafgreen-regional.json"
    assert compiled["version_group"] == "firered-leafgreen-regional"
    assert compiled["generation"] == 3
    assert compiled["label"] == "FireRed / LeafGreen (Regional Dex)"
    assert "thunderbolt" in compiled["unlimited_tms"]
    assert clefairy["types"] == ["normal"]
    assert clefairy["is_fully_evolved"] is False
    assert moves["charm"]["type"] == "normal"
    assert moves["tackle"]["power"] == 35


def test_compile_version_group_keeps_gen4_modern_types(monkeypatch, tmp_path):
    monkeypatch.setattr(pokedex, "COMPILED_DIR", tmp_path)
    monkeypatch.setattr(
        pokedex, "time", type("FakeTime", (), {"sleep": staticmethod(lambda _: None)})
    )
    monkeypatch.setattr(
        pokedex,
        "_merge_pre_evolution_moves",
        lambda compiled_pokemon, pre_evo_map=None: None,
    )
    monkeypatch.setattr(
        pokedex,
        "get_pokemon_list",
        lambda version_group: [
            {
                "entry_number": 172,
                "name": "pichu",
                "species_url": "https://pokeapi.co/api/v2/pokemon-species/172/",
            }
        ],
    )
    monkeypatch.setattr(
        pokedex,
        "get_pokemon_detail",
        lambda name: {
            "id": 172,
            "name": name,
            "types": ["electric"],
            "past_types": [],
            "base_stats": {
                "hp": 20,
                "attack": 40,
                "defense": 15,
                "special-attack": 35,
                "special-defense": 35,
                "speed": 60,
            },
            "abilities": ["static"],
        },
    )
    monkeypatch.setattr(
        pokedex,
        "get_species_detail",
        lambda name: {
            "name": name,
            "is_legendary": False,
            "evolves_from_species": None,
            "default_pokemon_name": name,
        },
    )
    monkeypatch.setattr(
        pokedex,
        "get_pokemon_moves",
        lambda name, version_group: [
            {
                "name": "thunder-punch",
                "learn_methods": ["machine"],
                "level_learned_at": 0,
            }
        ],
    )
    monkeypatch.setattr(
        pokedex,
        "get_move_detail",
        lambda name: {
            "name": name,
            "type": "electric",
            "power": 75,
            "accuracy": 100,
            "pp": 15,
            "damage_class": "physical",
            "past_values": [],
        },
    )

    out_path = pokedex.compile_version_group("brilliant-diamond-shining-pearl-regional")
    compiled = json.loads(out_path.read_text())
    pichu = compiled["pokemon"][0]
    move = pichu["moves"][0]

    assert compiled["version_group"] == "brilliant-diamond-shining-pearl-regional"
    assert compiled["generation"] == 8
    assert compiled["label"] == "Brilliant Diamond / Shining Pearl (Regional Dex)"
    assert "thunderbolt" in compiled["unlimited_tms"]
    assert pichu["types"] == ["electric"]
    assert move["type"] == "electric"
    assert move["power"] == 75


def test_compile_version_group_uses_profile_move_source_override(monkeypatch, tmp_path):
    monkeypatch.setattr(pokedex, "COMPILED_DIR", tmp_path)
    monkeypatch.setattr(
        pokedex,
        "time",
        type("FakeTime", (), {"sleep": staticmethod(lambda _: None)}),
    )
    monkeypatch.setattr(
        pokedex,
        "_merge_pre_evolution_moves",
        lambda compiled_pokemon, pre_evo_map=None: None,
    )
    monkeypatch.setattr(
        pokedex,
        "get_pokemon_list",
        lambda version_group: [
            {
                "entry_number": 150,
                "name": "mewtwo",
                "species_url": "https://pokeapi.co/api/v2/pokemon-species/150/",
            }
        ],
    )
    monkeypatch.setattr(
        pokedex,
        "get_pokemon_detail",
        lambda name: {
            "id": 150,
            "name": name,
            "types": ["psychic"],
            "base_stats": {
                "hp": 106,
                "attack": 110,
                "defense": 90,
                "special-attack": 154,
                "special-defense": 90,
                "speed": 130,
            },
            "abilities": ["pressure"],
        },
    )
    monkeypatch.setattr(
        pokedex,
        "get_species_detail",
        lambda name: {
            "name": name,
            "is_legendary": True,
            "evolves_from_species": None,
            "default_pokemon_name": name,
        },
    )

    seen_version_groups = []

    def fake_get_pokemon_moves(name, version_group):
        seen_version_groups.append(version_group)
        return [
            {
                "name": "psychic",
                "learn_methods": ["level-up"],
                "level_learned_at": 1,
            }
        ]

    monkeypatch.setattr(pokedex, "get_pokemon_moves", fake_get_pokemon_moves)
    monkeypatch.setattr(
        pokedex,
        "get_move_detail",
        lambda name: {
            "name": name,
            "type": "psychic",
            "power": 90,
            "accuracy": 100,
            "pp": 10,
            "damage_class": "special",
        },
    )

    out_path = pokedex.compile_version_group("firered-leafgreen-national")
    compiled = json.loads(out_path.read_text())

    assert out_path == tmp_path / "firered-leafgreen-national.json"
    assert compiled["version_group"] == "firered-leafgreen-national"
    assert compiled["label"] == "FireRed / LeafGreen (National Dex)"
    assert seen_version_groups == ["firered-leafgreen"]


def test_compile_version_group_respects_max_dex_and_fully_evolved_only(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(pokedex, "COMPILED_DIR", tmp_path)
    monkeypatch.setattr(
        pokedex,
        "time",
        type("FakeTime", (), {"sleep": staticmethod(lambda _: None)}),
    )

    ids = {"piplup": 393, "prinplup": 394, "empoleon": 395, "victini": 494}

    monkeypatch.setattr(
        pokedex,
        "get_pokemon_list",
        lambda version_group: [
            {"entry_number": 393, "name": "piplup", "species_url": "..."},
            {"entry_number": 394, "name": "prinplup", "species_url": "..."},
            {"entry_number": 395, "name": "empoleon", "species_url": "..."},
            {"entry_number": 494, "name": "victini", "species_url": "..."},
        ],
    )
    monkeypatch.setattr(
        pokedex,
        "get_pokemon_detail",
        lambda name: {
            "id": ids[name],
            "name": name,
            "types": ["water"] if name != "victini" else ["psychic", "fire"],
            "base_stats": {
                "hp": 84,
                "attack": 86,
                "defense": 88,
                "special-attack": 111,
                "special-defense": 101,
                "speed": 60,
            },
            "abilities": ["torrent"],
        },
    )
    evos = {
        "piplup": None,
        "prinplup": "piplup",
        "empoleon": "prinplup",
        "victini": None,
    }
    monkeypatch.setattr(
        pokedex,
        "get_species_detail",
        lambda name: {
            "name": name,
            "is_legendary": name == "victini",
            "evolves_from_species": evos[name],
            "default_pokemon_name": name,
        },
    )
    monkeypatch.setattr(
        pokedex,
        "get_pokemon_moves",
        lambda name, version_group: [
            {"name": "surf", "learn_methods": ["machine"], "level_learned_at": 0}
        ],
    )
    monkeypatch.setattr(
        pokedex,
        "get_move_detail",
        lambda name: {
            "name": name,
            "type": "water",
            "power": 90,
            "accuracy": 100,
            "pp": 15,
            "damage_class": "special",
        },
    )

    out_path = pokedex.compile_version_group("brilliant-diamond-shining-pearl-national")
    compiled = json.loads(out_path.read_text())

    assert out_path == tmp_path / "brilliant-diamond-shining-pearl-national.json"
    names = [p["name"] for p in compiled["pokemon"]]
    assert "empoleon" in names
    assert "piplup" not in names
    assert "prinplup" not in names
    assert "victini" not in names
    assert all(p["is_fully_evolved"] for p in compiled["pokemon"])
