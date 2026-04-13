"""
Fetch obtainable Pokemon lists from PokeAPI by game version group and compile
them into local JSON snapshots for the optimizer.
"""

import json
import time
from pathlib import Path

import pokebase as pb
from pokebase import cache as pokebase_cache

from data.profiles import GAME_PROFILES

DEFAULT_VERSION_GROUP = "firered-leafgreen-regional"
COMPILED_DIR = Path(__file__).parent / "compiled"


def list_version_groups() -> list[str]:
    return list(GAME_PROFILES)


def get_profile(version_group: str) -> dict:
    if version_group not in GAME_PROFILES:
        available = ", ".join(GAME_PROFILES)
        raise ValueError(
            f"Unknown version group '{version_group}'. Available: {available}"
        )
    return GAME_PROFILES[version_group]


def get_unlimited_tms(version_group: str) -> set[str]:
    return set(get_profile(version_group)["unlimited_tms"])


def compiled_path_for(version_group: str) -> Path:
    return COMPILED_DIR / f"{version_group}.json"


_ROMAN_NUMERALS = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
}


def _parse_generation_name(name: str) -> int:
    """Convert 'generation-v' to 5."""
    suffix = name.rsplit("-", 1)[-1]
    return _ROMAN_NUMERALS[suffix]


_vg_generation_cache: dict[str, int] = {}


def _generation_for_version_group(vg_name: str) -> int:
    """Return the generation number for a PokeAPI version group name."""
    if vg_name not in _vg_generation_cache:
        gen_name = pb.version_group(vg_name).generation.name
        _vg_generation_cache[vg_name] = _parse_generation_name(gen_name)
    return _vg_generation_cache[vg_name]


def _resolve_pokemon_types(detail: dict, generation: int) -> list[str]:
    """Return the Pokemon's types as of the given generation."""
    for entry in detail.get("past_types", []):
        entry_gen = _parse_generation_name(entry["generation"])
        if generation <= entry_gen:
            return entry["types"]
    return detail["types"]


def _resolve_move_for_generation(move_detail: dict, generation: int) -> dict:
    """Return a copy of move_detail with power/type/accuracy adjusted for the generation."""
    resolved = dict(move_detail)
    past = resolved.pop("past_values", [])
    entries = sorted(
        past, key=lambda e: _generation_for_version_group(e["version_group"])
    )
    for entry in entries:
        vg_gen = _generation_for_version_group(entry["version_group"])
        if generation < vg_gen:
            if entry.get("power") is not None:
                resolved["power"] = entry["power"]
            if entry.get("type") is not None:
                resolved["type"] = entry["type"]
            if entry.get("accuracy") is not None:
                resolved["accuracy"] = entry["accuracy"]
            break
    return resolved


def get_pokemon_list(version_group: str) -> list[dict]:
    """Return Pokedex entries for the given version group."""
    profile = get_profile(version_group)
    dex = pb.pokedex(profile["pokedex"])
    pokemon = [
        {
            "entry_number": entry.entry_number,
            "name": entry.pokemon_species.name,
            "species_url": entry.pokemon_species.url,
        }
        for entry in dex.pokemon_entries
    ]
    pokemon.sort(key=lambda p: p["entry_number"])
    return pokemon


def get_pokemon_detail(name: str) -> dict:
    """Return the slim Pokemon record used by the compiler."""
    data = pb.pokemon(name)
    return {
        "id": data.id,
        "name": data.name,
        "types": [t.type.name for t in data.types],
        "base_stats": {s.stat.name: s.base_stat for s in data.stats},
        "abilities": [a.ability.name for a in data.abilities if not a.is_hidden],
        "past_types": [
            {
                "generation": pt.generation.name,
                "types": [t.type.name for t in pt.types],
            }
            for pt in data.past_types
        ],
    }


def get_species_detail(name: str) -> dict:
    species = pb.pokemon_species(name)
    evolves_from = species.evolves_from_species
    default_variety = next(
        (variety.pokemon.name for variety in species.varieties if variety.is_default),
        species.name,
    )
    return {
        "name": species.name,
        "is_legendary": bool(species.is_legendary or species.is_mythical),
        "evolves_from_species": evolves_from.name if evolves_from else None,
        "default_pokemon_name": default_variety,
    }


def get_pokemon_moves(name: str, version_group: str) -> list[dict]:
    """
    Return moves learnable by *name* in *version_group*.
    Each entry: {name, learn_methods, level_learned_at}.
    """
    data = pb.pokemon(name)
    results = []
    for entry in data.moves:
        methods = []
        level = 0
        for vgd in entry.version_group_details:
            if vgd.version_group.name == version_group:
                methods.append(vgd.move_learn_method.name)
                if vgd.level_learned_at > level:
                    level = vgd.level_learned_at
        if methods:
            results.append(
                {
                    "name": entry.move.name,
                    "learn_methods": sorted(set(methods)),
                    "level_learned_at": level,
                }
            )
    return results


def get_move_detail(name: str) -> dict:
    """Return the move record used by the compiler, including meta fields."""
    data = pb.move(name)
    en_entries = [e for e in data.effect_entries if e.language.name == "en"]
    effect_text = en_entries[0].effect if en_entries else ""
    short_effect = en_entries[0].short_effect if en_entries else ""
    return {
        "name": data.name,
        "type": data.type.name,
        "power": data.power,
        "accuracy": data.accuracy,
        "pp": data.pp,
        "damage_class": data.damage_class.name,
        "drain": data.meta.drain,
        "min_hits": data.meta.min_hits,
        "max_hits": data.meta.max_hits,
        "meta_category": data.meta.category.name,
        "stat_chance": data.meta.stat_chance,
        "stat_changes": [(sc.stat.name, sc.change) for sc in data.stat_changes],
        "priority": data.priority,
        "effect_text": effect_text,
        "short_effect": short_effect,
        "past_values": [
            {
                "power": pv.power,
                "accuracy": pv.accuracy,
                "type": pv.type.name if pv.type else None,
                "version_group": pv.version_group.name,
            }
            for pv in data.past_values
        ],
    }


def get_pre_evolution(name: str) -> str | None:
    return get_species_detail(name)["evolves_from_species"]


def _cache_files() -> list[Path]:
    """Return all shelve files backing the pokebase cache."""
    base = Path(pokebase_cache.API_CACHE)
    candidates = [
        base,
        base.with_suffix(".db"),
        base.with_suffix(".dir"),
        base.with_suffix(".bak"),
    ]
    return [p for p in candidates if p.exists()]


def _verify_cache() -> bool:
    """Return True if the pokebase shelve cache is readable."""
    files = _cache_files()
    if not files:
        return True
    import shelve

    try:
        with shelve.open(pokebase_cache.API_CACHE, flag="r") as db:
            for key in list(db.keys())[:3]:
                _ = db[key]
        return True
    except Exception:
        return False


def clear_cache() -> int:
    """Delete the pokebase API cache files if they exist."""
    files = _cache_files()
    for f in files:
        f.unlink()
    return len(files)


def _merge_pre_evolution_moves(
    compiled_pokemon: list[dict],
    pre_evo_map: dict[str, str | None] | None = None,
) -> None:
    """
    Merge learnsets from pre-evolutions into evolved forms.
    Mutates compiled_pokemon in place.
    """

    def _species_key(pokemon: dict) -> str:
        return pokemon.get("species_name", pokemon["name"])

    by_name: dict[str, dict] = {_species_key(p): p for p in compiled_pokemon}

    if pre_evo_map is None:
        pre_evo_map = {}
        total = len(compiled_pokemon)
        for i, p in enumerate(compiled_pokemon, 1):
            name = _species_key(p)
            print(f"  pre-evo check [{i}/{total}] {name}")
            pre_evo_map[name] = get_pre_evolution(name)

    def _ancestor_moves(name: str) -> list[dict]:
        moves: list[dict] = []
        cur = pre_evo_map.get(name)
        while cur and cur in by_name:
            moves.extend(by_name[cur]["moves"])
            cur = pre_evo_map.get(cur)
        return moves

    for p in compiled_pokemon:
        own_move_names = {m["name"] for m in p["moves"]}
        for ancestor_move in _ancestor_moves(_species_key(p)):
            if ancestor_move["name"] in own_move_names:
                continue
            inherited = dict(ancestor_move)
            inherited["learn_methods"] = ["pre-evolution"]
            inherited["tm_only"] = False
            p["moves"].append(inherited)
            own_move_names.add(inherited["name"])


def _build_move_record(
    move_name: str,
    move_detail: dict,
    raw_move: dict,
    profile: dict,
    generation: int,
) -> dict:
    md = _resolve_move_for_generation(move_detail, generation)
    if md["power"] is None and move_name in profile["variable_power_estimates"]:
        md["power"] = profile["variable_power_estimates"][move_name]

    effect = md.pop("effect_text", "")
    short_effect = md.pop("short_effect", "")
    drain = md.pop("drain", 0) or 0
    min_hits = md.pop("min_hits", None)
    max_hits = md.pop("max_hits", None)
    meta_cat = md.pop("meta_category", "")
    stat_chance = md.pop("stat_chance", 0) or 0
    raw_stat_changes = md.pop("stat_changes", [])
    priority = md.pop("priority", 0) or 0

    md["recoil_pct"] = abs(drain) / 100 if drain < 0 else 0
    md["multi_hit"] = (min_hits + max_hits) / 2 if min_hits and max_hits else 1.0
    md["is_low_priority"] = priority < 0
    md["is_multi_turn"] = "recharge" in effect or "charges for one turn" in effect
    md["is_lock_in"] = "forced to attack" in effect
    md["is_self_ko"] = "User faints" in effect
    md["is_delayed_attack"] = "turns later" in short_effect.lower()
    md["is_conditional"] = "can only be used" in effect.lower()

    negative_changes = [
        (stat, change) for stat, change in raw_stat_changes if change < 0
    ]
    if meta_cat == "damage-raise" and stat_chance >= 100 and negative_changes:
        md["self_stat_changes"] = negative_changes
    else:
        md["self_stat_changes"] = []

    learn_methods = raw_move["learn_methods"]
    tm_only = learn_methods == ["machine"] or learn_methods == ["tutor"]
    return {
        **md,
        "learn_methods": learn_methods,
        "level_learned_at": raw_move["level_learned_at"],
        "tm_only": tm_only,
    }


def compile_version_group(version_group: str) -> Path:
    """
    Fetch all Pokemon + moves for a version group, write compiled JSON,
    and return the path to the compiled file.
    """
    if not _verify_cache():
        print("Corrupted pokebase cache detected — clearing.")
        n = clear_cache()
        print(f"  Removed {n} cache file(s).")

    profile = get_profile(version_group)
    api_version_group = profile["api_version_group"]
    dex = get_pokemon_list(version_group)
    max_dex_number = profile.get("max_dex_number")
    if max_dex_number is not None:
        dex = [entry for entry in dex if entry["entry_number"] <= max_dex_number]

    for extra_name in profile.get("extra_pokemon", []):
        if not any(e["name"] == extra_name for e in dex):
            next_number = max((e["entry_number"] for e in dex), default=0) + 1
            dex.append({"entry_number": next_number, "name": extra_name})

    total = len(dex)

    move_cache: dict[str, dict] = {}
    species_detail_by_name: dict[str, dict] = {}
    pre_evo_map: dict[str, str | None] = {}
    child_species = set()

    print("Collecting species metadata…")
    for i, entry in enumerate(dex, 1):
        name = entry["name"]
        print(f"  species [{i}/{total}] {name}")
        species = get_species_detail(name)
        species_detail_by_name[name] = species
        pre_evo_map[name] = species["evolves_from_species"]
        if species["evolves_from_species"]:
            child_species.add(species["evolves_from_species"])

    fully_evolved_only = profile.get("fully_evolved_only", False)
    if fully_evolved_only:
        dex = [entry for entry in dex if entry["name"] not in child_species]
        total = len(dex)
        print(f"Filtered to {total} fully-evolved species")

    compiled_pokemon = []
    for i, entry in enumerate(dex, 1):
        species_name = entry["name"]
        species = species_detail_by_name[species_name]
        pokemon_name = species["default_pokemon_name"]
        print(f"[{i}/{total}] {pokemon_name}")

        detail = get_pokemon_detail(pokemon_name)
        raw_moves = get_pokemon_moves(pokemon_name, api_version_group)
        generation = profile["generation"]
        types = _resolve_pokemon_types(detail, generation)

        moves = []
        for raw_move in raw_moves:
            move_name = raw_move["name"]
            if move_name not in move_cache:
                time.sleep(0.05)
                move_cache[move_name] = get_move_detail(move_name)
            moves.append(
                _build_move_record(
                    move_name,
                    move_cache[move_name],
                    raw_move,
                    profile,
                    generation,
                )
            )

        compiled_pokemon.append(
            {
                "id": detail["id"],
                "name": detail["name"],
                "species_name": species_name,
                "dex_number": entry["entry_number"],
                "types": types,
                "base_stats": detail["base_stats"],
                "abilities": detail["abilities"],
                "is_legendary": species["is_legendary"],
                "is_fully_evolved": not fully_evolved_only,
                "moves": moves,
            }
        )

    if not fully_evolved_only:
        for pokemon in compiled_pokemon:
            pokemon["is_fully_evolved"] = pokemon["species_name"] not in child_species
        print("\nMerging pre-evolution moves…")
        _merge_pre_evolution_moves(compiled_pokemon, pre_evo_map=pre_evo_map)
    else:
        for pokemon in compiled_pokemon:
            pokemon["is_fully_evolved"] = True

    output = {
        "version_group": version_group,
        "generation": profile["generation"],
        "label": profile["label"],
        "unlimited_tms": sorted(profile["unlimited_tms"]),
        "pokemon": compiled_pokemon,
    }

    COMPILED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = compiled_path_for(version_group)
    out_path.write_text(json.dumps(output, indent=2))
    print(
        f"\nWrote {out_path} ({len(compiled_pokemon)} pokemon, {len(move_cache)} unique moves)"
    )
    return out_path


def main():
    import sys

    usage = (
        "Usage:\n"
        "  python pokedex.py list [VERSION_GROUP]    — list pokemon names\n"
        "  python pokedex.py compile [VERSION_GROUP]  — fetch all data + moves, write compiled JSON\n"
        "  python pokedex.py clear-cache              — delete cached pokebase API cache\n"
    )

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    vg = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_VERSION_GROUP

    if cmd == "list":
        pokemon = get_pokemon_list(vg)
        print(json.dumps(pokemon, indent=2))
    elif cmd == "compile":
        compile_version_group(vg)
    elif cmd == "clear-cache":
        n = clear_cache()
        print(f"Removed {n} cached file(s).")
    else:
        print(usage)


if __name__ == "__main__":
    main()
