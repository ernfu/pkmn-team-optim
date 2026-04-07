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

# Hard-coded discounts for moves whose raw damage overstates optimizer value.
# Self-KO moves trade away the user, and Frustration assumes intentionally
# minimized friendship, so both get less credit than base power alone suggests.
MOVE_SCORE_FACTORS: dict[str, float] = {
    "self-destruct": 0.35,
    "explosion": 0.35,
    "frustration": 0.1,
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


# Gen 3 resisted chart: attacking_type -> set of defending types it is not very effective against.
NVE_CHART: dict[str, set[str]] = {
    "normal": {"rock", "steel"},
    "fire": {"fire", "water", "rock", "dragon"},
    "water": {"water", "grass", "dragon"},
    "electric": {"electric", "grass", "dragon"},
    "grass": {"fire", "grass", "poison", "flying", "bug", "dragon", "steel"},
    "ice": {"fire", "water", "ice", "steel"},
    "fighting": {"poison", "flying", "psychic", "bug"},
    "poison": {"poison", "ground", "rock", "ghost"},
    "ground": {"grass", "bug"},
    "flying": {"electric", "rock", "steel"},
    "psychic": {"psychic", "steel"},
    "bug": {"fire", "fighting", "poison", "flying", "ghost", "steel"},
    "rock": {"fighting", "ground", "steel"},
    "ghost": {"dark", "steel"},
    "dragon": {"steel"},
    "dark": {"fighting", "dark", "steel"},
    "steel": {"fire", "water", "electric", "steel"},
}

# Gen 3 immunity chart: attacking_type -> set of defending types it cannot hit.
IMMUNE_CHART: dict[str, set[str]] = {
    "normal": {"ghost"},
    "electric": {"ground"},
    "fighting": {"ghost"},
    "poison": {"steel"},
    "ground": {"flying"},
    "psychic": {"dark"},
    "ghost": {"normal"},
}


def is_super_effective(atk_type: str, def_type: str) -> bool:
    return def_type in SE_CHART.get(atk_type, set())


def type_multiplier(atk_type: str, def_type: str) -> float:
    """Return the Gen 3 monotype effectiveness multiplier."""
    if def_type in IMMUNE_CHART.get(atk_type, set()):
        return 0.0
    if def_type in SE_CHART.get(atk_type, set()):
        return 2.0
    if def_type in NVE_CHART.get(atk_type, set()):
        return 0.5
    return 1.0


def estimate_damage(
    power: float,
    atk_base: int,
    move_type: str,
    attacker_types: list[str],
    def_type: str,
    defender_base: int = 100,
) -> int:
    """Approximate Gen 3 monotype damage at Lv100, 0 IV / 0 EV."""
    multiplier = type_multiplier(move_type, def_type)
    if power <= 0 or multiplier <= 0:
        return 0

    atk_stat = 2 * atk_base + 5
    def_stat = 2 * defender_base + 5
    base = (42 * power * atk_stat // def_stat) // 50 + 2
    stab = 1.5 if move_type in attacker_types else 1.0
    return int(base * stab * multiplier)


def has_4x_weakness(types: list[str]) -> bool:
    """Return True if a dual-type Pokémon has any 4x weakness."""
    if len(types) < 2:
        return False
    t1, t2 = types[0], types[1]
    return any(
        t1 in se_targets and t2 in se_targets for se_targets in SE_CHART.values()
    )


def _effective_power(move: dict, low_priority_factor: float = 0.3) -> float:
    """Rough effective-power used to rank moves of the same type."""
    power = move.get("power") or 0
    if power <= 0:
        return 0.0
    acc = move["accuracy"] if move.get("accuracy") is not None else 100
    multi_hit = move.get("multi_hit", 1.0)
    recoil = 1.0 - move.get("recoil_pct", 0)
    multi_turn = 0.5 if move.get("is_multi_turn") else 1.0
    priority = low_priority_factor if move.get("is_low_priority") else 1.0
    move_factor = MOVE_SCORE_FACTORS.get(move.get("name", ""), 1.0)
    return (
        power * multi_hit * (acc / 100) * recoil * multi_turn * priority * move_factor
    )


def _is_machine_or_tutor_move(move: dict) -> bool:
    methods = move.get("learn_methods", [])
    return "machine" in methods or "tutor" in methods or move.get("tm_only", False)


def filter_dominated_moves(
    pokemon_pool: list[dict],
    protected_moves_by_pokemon: dict[str, set[str]] | None = None,
    low_priority_factor: float = 0.3,
) -> list[dict]:
    """Return a new pool with heuristic same-type pruning applied.

    For each Pokemon and attacking type, non-machine/non-tutor attacking moves
    are kept if their effective-power score is at least 80% of the best move of
    that type. TM/HM/tutor moves are preserved to keep resource semantics, and
    protected moves (for example user-locked moves) are always kept.
    """
    filtered: list[dict] = []
    protected_moves_by_pokemon = protected_moves_by_pokemon or {}
    keep_threshold = 0.8

    for poke in pokemon_pool:
        protected_moves = set(protected_moves_by_pokemon.get(poke["name"], set()))
        comparable_by_type: dict[str, list[dict]] = {}
        pruned_moves: set[str] = set()

        for move in poke["moves"]:
            power = move.get("power") or 0
            if power <= 0:
                continue
            if _is_machine_or_tutor_move(move):
                continue
            comparable_by_type.setdefault(move["type"], []).append(move)

        for same_type_moves in comparable_by_type.values():
            move_scores = {
                move["name"]: _effective_power(
                    move, low_priority_factor=low_priority_factor
                )
                for move in same_type_moves
            }
            best_score = max(move_scores.values(), default=0.0)
            for move in same_type_moves:
                if move["name"] in protected_moves:
                    continue
                if move_scores[move["name"]] < keep_threshold * best_score:
                    pruned_moves.add(move["name"])

        filtered.append(
            {
                **poke,
                "moves": [
                    move
                    for move in poke["moves"]
                    if move["name"] in protected_moves
                    or (move.get("power") or 0) <= 0
                    or _is_machine_or_tutor_move(move)
                    or move["name"] not in pruned_moves
                ],
            }
        )
    return filtered


def compute_scores(
    pokemon_pool: list[dict],
    acc_exponent: float = 2.0,
    speed_bonus: float = 0.1,
    low_priority_factor: float = 0.3,
) -> dict[tuple[str, str, str], float]:
    """
    Pre-compute S_{p,m,t} for every (pokemon, move, defending_type) triple.

    Only neutral-or-better (>=1x) entries are stored.  Not-very-effective
    (0.5x) and immune (0x) matchups are skipped to keep the model small.
    Returns a dict keyed by (pokemon_name, move_name, def_type).

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
            power_adj = (
                (m_power * multi_hit) / 2
                if move.get("is_multi_turn")
                else m_power * multi_hit
            )
            stab = 1.5 if m_type in p_types else 1.0
            stat = p_atk if m_type in PHYSICAL_TYPES else p_spa
            acc_factor = (m_acc / 100) ** acc_exponent

            recoil_factor = 1.0 - move["recoil_pct"]

            is_low_pri = move["is_low_priority"]
            priority_factor = low_priority_factor if is_low_pri else 1.0
            move_factor = MOVE_SCORE_FACTORS.get(m_name, 1.0)

            for def_type in ALL_TYPES:
                effectiveness = type_multiplier(m_type, def_type)
                if effectiveness < 1.0:
                    continue
                score = (
                    power_adj
                    * acc_factor
                    * stab
                    * effectiveness
                    * stat
                    * speed_factor
                    * recoil_factor
                    * priority_factor
                    * move_factor
                )
                key = (p_name, m_name, def_type)
                if key not in scores or score > scores[key]:
                    scores[key] = score

    return scores
