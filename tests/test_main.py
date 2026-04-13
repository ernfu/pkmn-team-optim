import json

from optimiser.main import (
    dataset_generation,
    dataset_unlimited_tms,
    load_dataset,
    load_pokemon,
)


def test_load_pokemon_filters_fully_evolved_legendaries_and_4x(tmp_path):
    data_path = tmp_path / "compiled.json"
    data_path.write_text(
        json.dumps(
            {
                "version_group": "test",
                "generation": 3,
                "pokemon": [
                    {
                        "name": "venusaur",
                        "types": ["grass", "poison"],
                        "is_fully_evolved": True,
                        "is_legendary": False,
                    },
                    {
                        "name": "charizard",
                        "types": ["fire", "flying"],
                        "is_fully_evolved": True,
                        "is_legendary": False,
                    },
                    {
                        "name": "mewtwo",
                        "types": ["psychic"],
                        "is_fully_evolved": True,
                        "is_legendary": True,
                    },
                    {
                        "name": "ivysaur",
                        "types": ["grass", "poison"],
                        "is_fully_evolved": False,
                        "is_legendary": False,
                    },
                ],
            }
        )
    )

    default_pool = load_pokemon(data_path, no_legendaries=True)
    assert [p["name"] for p in default_pool] == ["venusaur", "charizard"]

    no_4x_pool = load_pokemon(
        data_path,
        no_legendaries=False,
        no_4x_weakness=True,
    )
    assert [p["name"] for p in no_4x_pool] == ["venusaur", "mewtwo"]


def test_dataset_generation_prefers_current_profile_generation():
    data = {
        "version_group": "brilliant-diamond-shining-pearl-regional",
        "generation": 4,
        "pokemon": [],
    }

    assert dataset_generation(data) == 8


def test_dataset_generation_falls_back_to_json_field():
    data = {"version_group": "unknown-game", "generation": 5, "pokemon": []}
    assert dataset_generation(data) == 5


def test_dataset_generation_defaults_to_3():
    data = {"version_group": "unknown-game", "pokemon": []}
    assert dataset_generation(data) == 3


def test_load_dataset_fills_defaults(tmp_path):
    data_path = tmp_path / "minimal.json"
    data_path.write_text(json.dumps({"pokemon": []}))

    data = load_dataset(data_path)
    assert data["generation"] == 3
    assert isinstance(data["unlimited_tms"], list)
    assert "version_group" in data
    assert "label" in data


def test_dataset_unlimited_tms():
    data = {"unlimited_tms": ["ice-beam", "thunderbolt"]}
    result = dataset_unlimited_tms(data)
    assert result == {"ice-beam", "thunderbolt"}


def test_dataset_unlimited_tms_empty():
    assert dataset_unlimited_tms({}) == set()


def test_load_pokemon_4x_weakness_uses_generation(tmp_path):
    data_path = tmp_path / "compiled.json"
    data_path.write_text(
        json.dumps(
            {
                "version_group": "brilliant-diamond-shining-pearl-regional",
                "generation": 8,
                "pokemon": [
                    {
                        "name": "articuno",
                        "types": ["ice", "flying"],
                        "is_fully_evolved": True,
                        "is_legendary": False,
                    },
                    {
                        "name": "haxorus",
                        "types": ["dragon"],
                        "is_fully_evolved": True,
                        "is_legendary": False,
                    },
                ],
            }
        )
    )

    pool = load_pokemon(data_path, no_legendaries=False, no_4x_weakness=True)
    assert all(p["name"] != "articuno" for p in pool)
    assert any(p["name"] == "haxorus" for p in pool)
