"""
Fetch obtainable Pokémon lists from PokeAPI by game version group.
Results are cached locally to avoid repeat API calls.
"""

import json
import time
from pathlib import Path
from urllib.request import urlopen, Request

# ── Config ───────────────────────────────────────────────────────────────────

BASE_URL = "https://pokeapi.co/api/v2"
CACHE_DIR = Path(__file__).parent / ".pokeapi_cache"
CACHE_TTL = 60 * 60 * 24 * 7  # 7 days in seconds

# Maps a friendly name → the pokedex id/name used by PokeAPI.
# Add new entries here to support more games.
VERSION_GROUPS = {
    "firered-leafgreen": {
        "pokedex": "kanto",
        "label": "FireRed / LeafGreen",
    },
    "red-blue": {
        "pokedex": "kanto",
        "label": "Red / Blue",
    },
    "gold-silver": {
        "pokedex": "original-johto",
        "label": "Gold / Silver",
    },
    "ruby-sapphire": {
        "pokedex": "hoenn",
        "label": "Ruby / Sapphire",
    },
    "diamond-pearl": {
        "pokedex": "original-sinnoh",
        "label": "Diamond / Pearl",
    },
    "black-white": {
        "pokedex": "original-unova",
        "label": "Black / White",
    },
    "x-y": {
        "pokedex": "kalos-central",
        "label": "X / Y (Central Kalos)",
    },
    "sun-moon": {
        "pokedex": "original-alola",
        "label": "Sun / Moon",
    },
    "sword-shield": {
        "pokedex": "galar",
        "label": "Sword / Shield",
    },
}

# ── Gen 3 corrections ─────────────────────────────────────────────────────────
# Fairy didn't exist in Gen 3; these Pokémon had different typings.
GEN3_TYPE_OVERRIDES = {
    "clefairy": ["normal"],
    "clefable": ["normal"],
    "jigglypuff": ["normal"],
    "wigglytuff": ["normal"],
    "mr-mime": ["psychic"],
}

# Moves retroactively retyped to Fairy in Gen 6+; revert to Gen 3 type.
GEN3_MOVE_TYPE_OVERRIDES = {
    "charm": "normal",
    "moonlight": "normal",
    "sweet-kiss": "normal",
}

# Moves whose base power changed after Gen 3; revert to Gen 3 values.
GEN3_POWER_OVERRIDES = {
    "jump-kick": 70,  # 70 → 100 in Gen 5
    "high-jump-kick": 85,  # 85 → 130 in Gen 5
    "hi-jump-kick": 85,  # 85 → 130 in Gen 5
    "thrash": 90,  # 90 → 120 in Gen 5
    "petal-dance": 70,  # 70 → 120 in Gen 5
    "fire-spin": 15,  # 15 → 35 in Gen 5
    "tackle": 35,  # 35 → 40 (Gen 5) → 50 (Gen 6)
    "vine-whip": 35,  # 35 → 45 in Gen 6
    "knock-off": 20,  # 20 → 65 in Gen 6
    "lick": 20,  # 20 → 30 in Gen 6
    "pin-missile": 14,  # 14 → 25 in Gen 6
    "skull-bash": 100,  # 100 → 130 in Gen 6
    "crabhammer": 90,  # 90 → 100 in Gen 6
    "dig": 60,  # 60 → 80 later
    "thief": 40,
}

KANTO_LEGENDARIES = {"articuno", "zapdos", "moltres", "mewtwo", "mew"}

KANTO_FULLY_EVOLVED = {
    "venusaur",
    "charizard",
    "blastoise",
    "butterfree",
    "beedrill",
    "pidgeot",
    "raticate",
    "fearow",
    "arbok",
    "raichu",
    "sandslash",
    "nidoqueen",
    "nidoking",
    "clefable",
    "ninetales",
    "wigglytuff",
    "vileplume",
    "parasect",
    "venomoth",
    "dugtrio",
    "persian",
    "golduck",
    "primeape",
    "arcanine",
    "poliwrath",
    "alakazam",
    "machamp",
    "victreebel",
    "tentacruel",
    "golem",
    "rapidash",
    "slowbro",
    "magneton",
    "farfetchd",
    "dodrio",
    "dewgong",
    "muk",
    "cloyster",
    "gengar",
    "hypno",
    "kingler",
    "electrode",
    "exeggutor",
    "marowak",
    "hitmonlee",
    "hitmonchan",
    "lickitung",
    "weezing",
    "rhydon",
    "chansey",
    "tangela",
    "kangaskhan",
    "seaking",
    "starmie",
    "mr-mime",
    "scyther",
    "jynx",
    "electabuzz",
    "magmar",
    "pinsir",
    "tauros",
    "gyarados",
    "lapras",
    "ditto",
    "vaporeon",
    "jolteon",
    "flareon",
    "porygon",
    "omastar",
    "kabutops",
    "aerodactyl",
    "snorlax",
    "articuno",
    "zapdos",
    "moltres",
    "dragonite",
    "mewtwo",
    "mew",
}

VARIABLE_POWER_ESTIMATES = {
    "return": 102,  # max happiness (standard competitive assumption)
    "frustration": 102,  # min happiness (inverse of return)
    "low-kick": 70,  # conservative avg across typical fully-evolved weights
    "magnitude": 71,  # weighted average across magnitude rolls
    "flail": 40,  # situational; low expected value outside gimmick sets
    "reversal": 40,  # same as flail
}

MULTI_TURN_MOVES = {
    "hyper-beam",
    "blast-burn",
    "frenzy-plant",
    "hydro-cannon",
    "solar-beam",
    "skull-bash",
    "sky-attack",
    "razor-wind",
    "dream-eater",
    "future-sight",
}

RECOIL_MOVES = {
    "double-edge": 0.33,
    "take-down": 0.25,
    "submission": 0.25,
}

LOW_PRIORITY_MOVES = {
    "focus-punch",
}

# Multi-hit moves → expected number of hits.
# Gen 3 2-5 hit distribution: 2 (3/8), 3 (3/8), 4 (1/8), 5 (1/8) → E = 3.0
MULTI_HIT_MOVES = {
    "pin-missile": 3.0,
    "bone-rush": 3.0,
    "rock-blast": 3.0,
    "bullet-seed": 3.0,
    "icicle-spear": 3.0,
    "fury-attack": 3.0,
    "fury-swipes": 3.0,
    "spike-cannon": 3.0,
    "comet-punch": 3.0,
    "double-slap": 3.0,
    "barrage": 3.0,
    "bonemerang": 2.0,
    "double-kick": 2.0,
    "twineedle": 2.0,
}

# TMs that can be purchased repeatedly (Game Corner / Dept Store) in FRLG.
# All other TMs are single-use.
FRLG_UNLIMITED_TMS = {
    # Game Corner (Celadon)
    "ice-beam",
    "thunderbolt",
    "flamethrower",
    "iron-tail",
    "shadow-ball",
    # Celadon Dept Store
    "hyper-beam",
    "dig",
    "brick-break",
    "rest",
    "secret-power",
    "attract",
    "roar",
}

# ── Caching layer ────────────────────────────────────────────────────────────


COMPILED_DIR = Path(__file__).parent / "compiled"


def _cache_path(key: str) -> Path:
    """Return the filesystem path for a cache key (supports hierarchical keys)."""
    safe_key = key.replace("?", "_")
    return CACHE_DIR / f"{safe_key}.json"


def _read_cache(key: str) -> dict | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if time.time() - data["ts"] > CACHE_TTL:
        path.unlink()
        return None
    return data["payload"]


def _write_cache(key: str, payload: dict) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": time.time(), "payload": payload}))


# ── API helpers ──────────────────────────────────────────────────────────────


def api_get(endpoint: str) -> dict:
    """GET a PokeAPI endpoint with local file caching."""
    cached = _read_cache(endpoint)
    if cached is not None:
        return cached

    url = f"{BASE_URL}/{endpoint}"
    print(f"  ↳ fetching {url}")
    req = Request(url, headers={"User-Agent": "pokedex-fetcher/1.0"})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    _write_cache(endpoint, data)
    return data


# ── Core logic ───────────────────────────────────────────────────────────────


def get_pokemon_list(version_group: str) -> list[dict]:
    """
    Return a list of pokemon dicts for the given version group.

    Each dict:
        {
            "entry_number": 1,
            "name": "bulbasaur",
            "species_url": "https://pokeapi.co/api/v2/pokemon-species/1/"
        }
    """
    if version_group not in VERSION_GROUPS:
        available = ", ".join(VERSION_GROUPS)
        raise ValueError(
            f"Unknown version group '{version_group}'. " f"Available: {available}"
        )

    pokedex_name = VERSION_GROUPS[version_group]["pokedex"]
    data = api_get(f"pokedex/{pokedex_name}")

    pokemon = []
    for entry in data["pokemon_entries"]:
        pokemon.append(
            {
                "entry_number": entry["entry_number"],
                "name": entry["pokemon_species"]["name"],
                "species_url": entry["pokemon_species"]["url"],
            }
        )

    pokemon.sort(key=lambda p: p["entry_number"])
    return pokemon


def get_pokemon_detail(name: str) -> dict:
    """
    Fetch /pokemon/{name} and return a slim dict:
    {id, name, types, base_stats, abilities}.
    """
    data = api_get(f"pokemon/{name}")
    return {
        "id": data["id"],
        "name": data["name"],
        "types": [t["type"]["name"] for t in data["types"]],
        "base_stats": {s["stat"]["name"]: s["base_stat"] for s in data["stats"]},
        "abilities": [
            a["ability"]["name"] for a in data["abilities"] if not a["is_hidden"]
        ],
    }


def get_pokemon_moves(name: str, version_group: str) -> list[dict]:
    """
    Return moves learnable by *name* in *version_group*.
    Each entry: {name, learn_methods, level_learned_at}.
    Collects ALL learn methods per move (level-up, machine, tutor, egg).
    """
    data = api_get(f"pokemon/{name}")
    results = []
    for entry in data["moves"]:
        methods = []
        level = 0
        for vgd in entry["version_group_details"]:
            if vgd["version_group"]["name"] == version_group:
                methods.append(vgd["move_learn_method"]["name"])
                if vgd["level_learned_at"] > level:
                    level = vgd["level_learned_at"]
        if methods:
            results.append(
                {
                    "name": entry["move"]["name"],
                    "learn_methods": sorted(set(methods)),
                    "level_learned_at": level,
                }
            )
    return results


def get_move_detail(name: str) -> dict:
    """
    Fetch /move/{name} and return:
    {name, type, power, accuracy, pp, damage_class}.
    """
    data = api_get(f"move/{name}")
    return {
        "name": data["name"],
        "type": data["type"]["name"],
        "power": data["power"],
        "accuracy": data["accuracy"],
        "pp": data["pp"],
        "damage_class": data["damage_class"]["name"],
    }


def get_pre_evolution(name: str) -> str | None:
    """Return the name of the species this Pokémon evolves from, or None."""
    data = api_get(f"pokemon-species/{name}")
    evo = data.get("evolves_from_species")
    return evo["name"] if evo else None


def clear_cache() -> int:
    """Delete all cached files (recursive). Returns count of files removed."""
    if not CACHE_DIR.exists():
        return 0
    files = list(CACHE_DIR.rglob("*.json"))
    for f in files:
        f.unlink()
    return len(files)


def _merge_pre_evolution_moves(compiled_pokemon: list[dict]) -> None:
    """
    For each evolved Pokémon, walk up the evolution chain and merge in any
    moves the pre-evolution(s) know that the evolved form doesn't already have.
    Mutates compiled_pokemon in place.
    """
    by_name: dict[str, dict] = {p["name"]: p for p in compiled_pokemon}

    pre_evo_map: dict[str, str | None] = {}
    total = len(compiled_pokemon)
    for i, p in enumerate(compiled_pokemon, 1):
        name = p["name"]
        print(f"  pre-evo check [{i}/{total}] {name}")
        pre_evo_map[name] = get_pre_evolution(name)

    def _ancestor_moves(name: str) -> list[dict]:
        """Collect moves from all ancestors up the chain."""
        moves: list[dict] = []
        cur = pre_evo_map.get(name)
        while cur and cur in by_name:
            moves.extend(by_name[cur]["moves"])
            cur = pre_evo_map.get(cur)
        return moves

    for p in compiled_pokemon:
        own_move_names = {m["name"] for m in p["moves"]}
        for ancestor_move in _ancestor_moves(p["name"]):
            if ancestor_move["name"] in own_move_names:
                continue
            inherited = dict(ancestor_move)
            inherited["learn_methods"] = ["pre-evolution"]
            inherited["tm_only"] = False
            p["moves"].append(inherited)
            own_move_names.add(inherited["name"])


def compile_version_group(version_group: str) -> Path:
    """
    Fetch all pokemon + moves for a version group, write compiled JSON.
    Returns the path to the compiled file.
    """
    cfg = VERSION_GROUPS[version_group]
    dex = get_pokemon_list(version_group)
    total = len(dex)

    move_cache: dict[str, dict] = {}
    compiled_pokemon = []

    for i, entry in enumerate(dex, 1):
        name = entry["name"]
        print(f"[{i}/{total}] {name}")

        detail = get_pokemon_detail(name)
        raw_moves = get_pokemon_moves(name, version_group)

        types = GEN3_TYPE_OVERRIDES.get(name, detail["types"])

        moves = []
        for rm in raw_moves:
            mname = rm["name"]
            if mname not in move_cache:
                time.sleep(0.1)
                move_cache[mname] = get_move_detail(mname)
            md = dict(move_cache[mname])
            if md["type"] == "fairy":
                md["type"] = GEN3_MOVE_TYPE_OVERRIDES.get(mname, "normal")
            if mname in GEN3_POWER_OVERRIDES:
                md["power"] = GEN3_POWER_OVERRIDES[mname]
            if md["power"] is None and mname in VARIABLE_POWER_ESTIMATES:
                md["power"] = VARIABLE_POWER_ESTIMATES[mname]
            md["is_multi_turn"] = mname in MULTI_TURN_MOVES
            md["recoil_pct"] = RECOIL_MOVES.get(mname, 0)
            md["is_low_priority"] = mname in LOW_PRIORITY_MOVES
            md["multi_hit"] = MULTI_HIT_MOVES.get(mname, 1.0)
            learn_methods = rm["learn_methods"]
            tm_only = learn_methods == ["machine"] or learn_methods == ["tutor"]
            moves.append(
                {
                    **md,
                    "learn_methods": learn_methods,
                    "level_learned_at": rm["level_learned_at"],
                    "tm_only": tm_only,
                }
            )

        compiled_pokemon.append(
            {
                "id": detail["id"],
                "name": detail["name"],
                "dex_number": entry["entry_number"],
                "types": types,
                "base_stats": detail["base_stats"],
                "abilities": detail["abilities"],
                "is_legendary": name in KANTO_LEGENDARIES,
                "is_fully_evolved": name in KANTO_FULLY_EVOLVED,
                "moves": moves,
            }
        )

    print("\nMerging pre-evolution moves…")
    _merge_pre_evolution_moves(compiled_pokemon)

    output = {
        "version_group": version_group,
        "label": cfg["label"],
        "pokemon": compiled_pokemon,
    }

    COMPILED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = COMPILED_DIR / f"{version_group}.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(
        f"\nWrote {out_path} ({len(compiled_pokemon)} pokemon, {len(move_cache)} unique moves)"
    )
    return out_path


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    import sys

    usage = (
        "Usage:\n"
        "  python pokedex.py list [VERSION_GROUP]    — list pokemon names\n"
        "  python pokedex.py compile [VERSION_GROUP]  — fetch all data + moves, write compiled JSON\n"
        "  python pokedex.py clear-cache              — delete cached API responses\n"
    )

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    vg = sys.argv[2] if len(sys.argv) > 2 else "firered-leafgreen"

    if cmd == "list":
        pokemon = get_pokemon_list(vg)
        print(json.dumps(pokemon, indent=2))
    elif cmd == "compile":
        compile_version_group(vg)
    elif cmd == "clear-cache":
        n = clear_cache()
        print(f"Removed {n} cached files.")
    else:
        print(usage)


if __name__ == "__main__":
    main()
