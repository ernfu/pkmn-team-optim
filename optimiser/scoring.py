"""
Generation-aware type chart, move classification, and score pre-computation.
"""

LEGACY_TYPES = [
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

MODERN_TYPES = LEGACY_TYPES + ["fairy"]

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

SELF_KO_FACTOR = 0.35
LOCK_IN_FACTOR = 0.5
DELAYED_ATTACK_FACTOR = 0.3
CONDITIONAL_MOVE_FACTOR = 0.2
SELF_STAT_DROP_PENALTY_PER_STAGE = 0.1

MOVE_SCORE_FACTORS: dict[str, float] = {
    "frustration": 0.1,
}


def move_penalty_factor(move: dict) -> float:
    """Return a combined multiplicative penalty derived from move metadata."""
    factor = MOVE_SCORE_FACTORS.get(move.get("name", ""), 1.0)
    if move.get("is_self_ko"):
        factor *= SELF_KO_FACTOR
    if move.get("is_lock_in"):
        factor *= LOCK_IN_FACTOR
    if move.get("is_delayed_attack"):
        factor *= DELAYED_ATTACK_FACTOR
    if move.get("is_conditional"):
        factor *= CONDITIONAL_MOVE_FACTOR
    stat_changes = move.get("self_stat_changes") or []
    total_drop = sum(abs(change) for _, change in stat_changes)
    if total_drop:
        factor *= max(1.0 - SELF_STAT_DROP_PENALTY_PER_STAGE * total_drop, 0.1)
    return factor


# Gen 3-5 super-effective chart: attacking_type -> set of defending types it is
# super-effective against.
LEGACY_SE_CHART: dict[str, set[str]] = {
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


# Gen 6+ super-effective chart, including Fairy.
MODERN_SE_CHART: dict[str, set[str]] = {
    **LEGACY_SE_CHART,
    "poison": {"grass", "fairy"},
    "steel": {"ice", "rock", "fairy"},
    "fairy": {"fighting", "dragon", "dark"},
}

# Gen 3-5 resisted chart: attacking_type -> set of defending types it is not very effective against.
LEGACY_NVE_CHART: dict[str, set[str]] = {
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

# Gen 6+ resisted chart, including Fairy and Steel interaction changes.
MODERN_NVE_CHART: dict[str, set[str]] = {
    **LEGACY_NVE_CHART,
    "fighting": {"poison", "flying", "psychic", "bug", "fairy"},
    "bug": {"fire", "fighting", "poison", "flying", "ghost", "steel", "fairy"},
    "ghost": {"dark"},
    "dark": {"fighting", "dark", "fairy"},
    "fairy": {"fire", "poison", "steel"},
}

# Gen 3-5 immunity chart: attacking_type -> set of defending types it cannot hit.
LEGACY_IMMUNE_CHART: dict[str, set[str]] = {
    "normal": {"ghost"},
    "electric": {"ground"},
    "fighting": {"ghost"},
    "poison": {"steel"},
    "ground": {"flying"},
    "psychic": {"dark"},
    "ghost": {"normal"},
}

MODERN_IMMUNE_CHART: dict[str, set[str]] = {
    **LEGACY_IMMUNE_CHART,
    "dragon": {"fairy"},
}

ALL_TYPES = LEGACY_TYPES
SUPPORTED_GENERATIONS = {3, 4, 5, 6, 7, 8, 9}


def types_for_generation(generation: int) -> list[str]:
    _validate_generation(generation)
    return MODERN_TYPES if generation >= 6 else LEGACY_TYPES


def _charts_for_generation(
    generation: int,
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    _validate_generation(generation)
    if generation >= 6:
        return MODERN_SE_CHART, MODERN_NVE_CHART, MODERN_IMMUNE_CHART
    return LEGACY_SE_CHART, LEGACY_NVE_CHART, LEGACY_IMMUNE_CHART


def _validate_generation(generation: int) -> None:
    if generation not in SUPPORTED_GENERATIONS:
        supported = ", ".join(str(g) for g in sorted(SUPPORTED_GENERATIONS))
        raise ValueError(f"Unsupported generation {generation}. Supported: {supported}")


def move_category(move: dict, generation: int = 3) -> str:
    _validate_generation(generation)
    if generation <= 3:
        if (move.get("power") or 0) <= 0:
            return move.get("damage_class", "status")
        return "physical" if move["type"] in PHYSICAL_TYPES else "special"
    return move.get("damage_class", "status")


def attack_stat_for_move(pokemon: dict, move: dict, generation: int = 3) -> int:
    category = move_category(move, generation=generation)
    if category == "physical":
        return pokemon["base_stats"]["attack"]
    return pokemon["base_stats"]["special-attack"]


def is_super_effective(atk_type: str, def_type: str, generation: int = 3) -> bool:
    se_chart, _, _ = _charts_for_generation(generation)
    return def_type in se_chart.get(atk_type, set())


def type_multiplier(atk_type: str, def_type: str, generation: int = 3) -> float:
    """Return the monotype effectiveness multiplier for the selected generation."""
    se_chart, nve_chart, immune_chart = _charts_for_generation(generation)
    if def_type in immune_chart.get(atk_type, set()):
        return 0.0
    if def_type in se_chart.get(atk_type, set()):
        return 2.0
    if def_type in nve_chart.get(atk_type, set()):
        return 0.5
    return 1.0


def estimate_damage(
    power: float,
    atk_base: int,
    move_type: str,
    attacker_types: list[str],
    def_type: str,
    defender_base: int = 100,
    generation: int = 3,
) -> int:
    """Approximate monotype damage at Lv100, 0 IV / 0 EV."""
    multiplier = type_multiplier(move_type, def_type, generation=generation)
    if power <= 0 or multiplier <= 0:
        return 0

    atk_stat = 2 * atk_base + 5
    def_stat = 2 * defender_base + 5
    base = (42 * power * atk_stat // def_stat) // 50 + 2
    stab = 1.5 if move_type in attacker_types else 1.0
    return int(base * stab * multiplier)


def has_4x_weakness(types: list[str], generation: int = 3) -> bool:
    """Return True if a dual-type Pokémon has any 4x weakness."""
    se_chart, _, _ = _charts_for_generation(generation)
    if len(types) < 2:
        return False
    t1, t2 = types[0], types[1]
    return any(
        t1 in se_targets and t2 in se_targets for se_targets in se_chart.values()
    )


def _effective_power(move: dict, low_priority_factor: float = 0.3) -> float:
    """Rough effective-power used to rank moves of the same type."""
    power = move.get("power") or 0
    if power <= 0:
        return 0.0
    acc = move["accuracy"] if move.get("accuracy") is not None else 100
    multi_hit = move.get("multi_hit", 1.0)
    recoil = 1.0 - move.get("recoil_pct", 0)
    multi_turn = 0.3 if move.get("is_multi_turn") else 1.0
    priority = low_priority_factor if move.get("is_low_priority") else 1.0
    penalty = move_penalty_factor(move)
    return power * multi_hit * (acc / 100) * recoil * multi_turn * priority * penalty


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
    generation: int = 3,
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
    _validate_generation(generation)
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
            stat = attack_stat_for_move(poke, move, generation=generation)
            acc_factor = (m_acc / 100) ** acc_exponent

            recoil_factor = 1.0 - move["recoil_pct"]

            is_low_pri = move["is_low_priority"]
            priority_factor = low_priority_factor if is_low_pri else 1.0
            move_factor = move_penalty_factor(move)

            for def_type in types_for_generation(generation):
                effectiveness = type_multiplier(m_type, def_type, generation=generation)
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
