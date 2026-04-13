"""
Generation-aware compiler profiles for supported Pokemon version groups.
"""

from copy import deepcopy

COMMON_BATTLE_PROFILE = {
    "variable_power_estimates": {
        "return": 102,
        "frustration": 102,
        "low-kick": 70,
        "magnitude": 71,
        "flail": 40,
        "reversal": 40,
    },
}

GENERATION_PROFILES = {
    3: {**COMMON_BATTLE_PROFILE},
    8: {**COMMON_BATTLE_PROFILE},
}

VERSION_GROUP_BASES = {
    "firered-leafgreen": {
        "generation": 3,
        "api_version_group": "firered-leafgreen",
        "label": "FireRed / LeafGreen",
        "regional_pokedex": "kanto",
        "national_max_dex": 386,
        "starter_candidates": {
            "venusaur",
            "charizard",
            "blastoise",
        },
        "unlimited_tms": {
            "ice-beam",
            "thunderbolt",
            "flamethrower",
            "iron-tail",
            "shadow-ball",
            "hyper-beam",
            "dig",
            "brick-break",
            "rest",
            "secret-power",
            "attract",
            "roar",
        },
    },
    "brilliant-diamond-shining-pearl": {
        "generation": 8,
        "api_version_group": "brilliant-diamond-shining-pearl",
        "label": "Brilliant Diamond / Shining Pearl",
        "regional_pokedex": "extended-sinnoh",
        "national_max_dex": 493,
        "extra_pokemon": ["jirachi"],
        "starter_candidates": {
            "torterra",
            "infernape",
            "empoleon",
        },
        "unlimited_tms": {
            "blizzard",
            "hyper-beam",
            "light-screen",
            "protect",
            "safeguard",
            "solar-beam",
            "thunder",
            "sunny-day",
            "ice-beam",
            "iron-tail",
            "thunderbolt",
        },
    },
}


def _expand_version_groups(bases: dict) -> dict:
    result: dict[str, dict] = {}
    for name, base in bases.items():
        common = {
            "generation": base["generation"],
            "api_version_group": base["api_version_group"],
            "starter_candidates": base["starter_candidates"],
            "unlimited_tms": base["unlimited_tms"],
            "extra_pokemon": base.get("extra_pokemon", []),
        }
        result[f"{name}-regional"] = {
            **common,
            "pokedex": base["regional_pokedex"],
            "label": f"{base['label']} (Regional Dex)",
        }
        result[f"{name}-national"] = {
            **common,
            "pokedex": "national",
            "max_dex_number": base["national_max_dex"],
            "fully_evolved_only": True,
            "source_version_group": f"{name}-regional",
            "label": f"{base['label']} (National Dex)",
        }
    return result


VERSION_GROUP_PROFILES = _expand_version_groups(VERSION_GROUP_BASES)


def _build_game_profiles() -> dict[str, dict]:
    game_profiles: dict[str, dict] = {}
    for version_group, version_profile in VERSION_GROUP_PROFILES.items():
        generation = version_profile["generation"]
        generation_profile = GENERATION_PROFILES[generation]
        merged = deepcopy(generation_profile)
        merged.update(deepcopy(version_profile))
        game_profiles[version_group] = merged
    return game_profiles


GAME_PROFILES = _build_game_profiles()

VERSION_GROUPS = {
    name: {"pokedex": profile["pokedex"], "label": profile["label"]}
    for name, profile in GAME_PROFILES.items()
}
