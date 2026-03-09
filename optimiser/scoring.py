"""
Gen 3 type chart, physical/special classification, and score pre-computation.
"""

ALL_TYPES = [
    "normal",
    "fire",
    "water",
    "electric",
    "grass",
    "ice",
    "fighting",
    "poison",
    "ground",
    "flying",
    "psychic",
    "bug",
    "rock",
    "ghost",
    "dragon",
    "dark",
    "steel",
]

PHYSICAL_TYPES = {
    "normal",
    "fighting",
    "flying",
    "poison",
    "ground",
    "rock",
    "bug",
    "ghost",
    "steel",
}
SPECIAL_TYPES = {
    "fire",
    "water",
    "electric",
    "grass",
    "ice",
    "psychic",
    "dragon",
    "dark",
}

# Gen 3 super-effective chart: attacking_type -> set of defending types it is SE against.
SE_CHART: dict[str, set[str]] = {
    "normal": set(),
    "fire": {"grass", "ice", "bug", "steel"},
    "water": {"fire", "ground", "rock"},
    "electric": {"water", "flying"},
    "grass": {"water", "ground", "rock"},
    "ice": {"grass", "ground", "flying", "dragon"},
    "fighting": {"normal", "ice", "rock", "dark", "steel"},
    "poison": {"grass"},
    "ground": {"fire", "electric", "poison", "rock", "steel"},
    "flying": {"grass", "fighting", "bug"},
    "psychic": {"fighting", "poison"},
    "bug": {"grass", "psychic", "dark"},
    "rock": {"fire", "ice", "flying", "bug"},
    "ghost": {"psychic", "ghost"},
    "dragon": {"dragon"},
    "dark": {"psychic", "ghost"},
    "steel": {"ice", "rock"},
}


def is_super_effective(atk_type: str, def_type: str) -> bool:
    return def_type in SE_CHART.get(atk_type, set())


def has_4x_weakness(types: list[str]) -> bool:
    """Return True if a dual-type Pokémon has any 4x weakness."""
    if len(types) < 2:
        return False
    t1, t2 = types[0], types[1]
    return any(
        t1 in se_targets and t2 in se_targets for se_targets in SE_CHART.values()
    )


def compute_scores(
    pokemon_pool: list[dict],
    acc_exponent: float = 2.0,
    speed_bonus: float = 0.1,
    low_priority_factor: float = 0.3,
) -> dict[tuple[str, str, str], float]:
    """
    Pre-compute S_{p,m,t} for every (pokemon, move, defending_type) triple.

    Only entries where the score > 0 (i.e. the move is SE against that type)
    are stored. Returns a dict keyed by (pokemon_name, move_name, def_type).

    acc_exponent controls how harshly low-accuracy moves are penalized:
    accuracy factor = (acc/100)^acc_exponent.  At 2.0, a 70% move gets 0.49.

    speed_bonus: fractional bonus the fastest Pokémon in the pool receives
    (0.1 = 10%).  Slowest gets 1.0×, linearly interpolated.

    low_priority_factor: multiplier for negative-priority moves like Focus
    Punch (0.3 = 30% credit).  Set to 1.0 to disable the penalty.
    """
    scores: dict[tuple[str, str, str], float] = {}

    speeds = [p["base_stats"]["speed"] for p in pokemon_pool]
    min_spd = min(speeds) if speeds else 1
    max_spd = max(speeds) if speeds else 1
    spd_range = max_spd - min_spd if max_spd > min_spd else 1

    for poke in pokemon_pool:
        p_name = poke["name"]
        p_types = poke["types"]
        p_atk = poke["base_stats"]["attack"]
        p_spa = poke["base_stats"]["special-attack"]
        p_spd = poke["base_stats"]["speed"]

        speed_factor = 1.0 + speed_bonus * (p_spd - min_spd) / spd_range

        for move in poke["moves"]:
            if not move["power"] or move["power"] <= 0:
                continue

            m_name = move["name"]
            m_type = move["type"]
            m_power = move["power"]
            m_acc = move["accuracy"] if move["accuracy"] is not None else 100

            multi_hit = move.get("multi_hit", 1.0)
            power_adj = (m_power * multi_hit) / 2 if move.get("is_multi_turn") else m_power * multi_hit
            stab = 1.5 if m_type in p_types else 1.0
            stat = p_atk if m_type in PHYSICAL_TYPES else p_spa
            acc_factor = (m_acc / 100) ** acc_exponent

            recoil_factor = 1.0 - move["recoil_pct"]

            is_low_pri = move["is_low_priority"]
            priority_factor = low_priority_factor if is_low_pri else 1.0

            for def_type in ALL_TYPES:
                if not is_super_effective(m_type, def_type):
                    continue
                effectiveness = 2.0
                score = (
                    power_adj
                    * acc_factor
                    * stab
                    * effectiveness
                    * stat
                    * speed_factor
                    * recoil_factor
                    * priority_factor
                )
                key = (p_name, m_name, def_type)
                if key not in scores or score > scores[key]:
                    scores[key] = score

    return scores
