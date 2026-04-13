"""
Parametrized tests that automatically cover every registered version group
and supported generation. Adding a new gen or version group to profiles.py
pulls it into this test matrix without any manual test updates.
"""

import json
from pathlib import Path

import pytest

from data.pokedex import get_profile, list_version_groups, compiled_path_for
from data.profiles import GAME_PROFILES, GENERATION_PROFILES
from optimiser.main import dataset_generation
from optimiser.scoring import (
    SUPPORTED_GENERATIONS,
    compute_scores,
    type_multiplier,
    types_for_generation,
)
from optimiser.solver import Params, build_model, solve_model

ALL_VERSION_GROUPS = list(GAME_PROFILES.keys())
ALL_GENERATIONS = sorted(SUPPORTED_GENERATIONS)
COMPILED_DIR = Path(__file__).resolve().parents[1] / "data" / "compiled"
COMPILED_FILES = sorted(COMPILED_DIR.glob("*.json"))

REQUIRED_PROFILE_KEYS = {
    "generation",
    "pokedex",
    "label",
    "starter_candidates",
    "unlimited_tms",
}
REQUIRED_BATTLE_KEYS = {
    "variable_power_estimates",
}
REQUIRED_POKEMON_KEYS = {
    "name",
    "types",
    "base_stats",
    "is_fully_evolved",
    "is_legendary",
    "moves",
}
REQUIRED_MOVE_KEYS = {
    "name",
    "type",
    "power",
    "accuracy",
    "damage_class",
    "learn_methods",
    "tm_only",
    "is_multi_turn",
    "recoil_pct",
    "is_low_priority",
    "multi_hit",
    "is_lock_in",
    "is_self_ko",
    "self_stat_changes",
}


# ---------------------------------------------------------------------------
# 1. Profile schema validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version_group", ALL_VERSION_GROUPS)
class TestProfileSchema:
    def test_get_profile_succeeds(self, version_group):
        profile = get_profile(version_group)
        assert isinstance(profile, dict)

    def test_has_required_keys(self, version_group):
        profile = get_profile(version_group)
        missing = REQUIRED_PROFILE_KEYS - profile.keys()
        assert not missing, f"Missing profile keys: {missing}"

    def test_has_battle_keys_from_generation_merge(self, version_group):
        profile = get_profile(version_group)
        missing = REQUIRED_BATTLE_KEYS - profile.keys()
        assert not missing, f"Missing battle keys: {missing}"

    def test_generation_is_supported(self, version_group):
        profile = get_profile(version_group)
        assert profile["generation"] in SUPPORTED_GENERATIONS

    def test_generation_has_profile(self, version_group):
        gen = get_profile(version_group)["generation"]
        assert (
            gen in GENERATION_PROFILES
        ), f"generation {gen} not in GENERATION_PROFILES"

    def test_source_version_group_is_valid(self, version_group):
        profile = get_profile(version_group)
        source = profile.get("source_version_group")
        if source is not None:
            assert (
                source in GAME_PROFILES
            ), f"source_version_group '{source}' not in GAME_PROFILES"


# ---------------------------------------------------------------------------
# 2. Type chart completeness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("generation", ALL_GENERATIONS)
class TestTypeChartCompleteness:
    def test_types_non_empty(self, generation):
        types = types_for_generation(generation)
        assert len(types) > 0

    def test_type_multiplier_no_errors(self, generation):
        types = types_for_generation(generation)
        for atk in types:
            for def_type in types:
                mult = type_multiplier(atk, def_type, generation=generation)
                assert mult >= 0.0

    def test_self_se_types_are_only_ghost_and_dragon(self, generation):
        """Ghost and Dragon are canonically SE against themselves; no other type should be."""
        types = types_for_generation(generation)
        self_se = {
            t for t in types if type_multiplier(t, t, generation=generation) > 1.0
        }
        assert self_se <= {"ghost", "dragon"}

    def test_compute_scores_with_synthetic_pool(self, generation):
        pool = [
            {
                "name": "test-mon",
                "types": ["normal"],
                "base_stats": {"attack": 100, "special-attack": 100, "speed": 80},
                "moves": [
                    {
                        "name": "test-move",
                        "type": "normal",
                        "power": 80,
                        "accuracy": 100,
                        "pp": 15,
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
                ],
            }
        ]
        scores = compute_scores(pool, generation=generation)
        assert len(scores) > 0


# ---------------------------------------------------------------------------
# 3. Solver feasibility
# ---------------------------------------------------------------------------


def _make_pool_and_scores(generation):
    """Build a minimal 6-pokemon pool with 4 distinct-type moves each."""
    types = types_for_generation(generation)
    move_types = (types * 4)[:4]
    pool = []
    scores = {}

    for idx in range(6):
        name = f"syn-{idx}"
        moves = []
        for mi, mt in enumerate(move_types):
            mname = f"{name}-m{mi}"
            moves.append(
                {
                    "name": mname,
                    "type": mt,
                    "power": 80,
                    "accuracy": 100,
                    "pp": 15,
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
            )
            for dt in types:
                scores[name, mname, dt] = 100.0

        pool.append(
            {
                "name": name,
                "types": ["normal"],
                "base_stats": {"attack": 100, "special-attack": 100, "speed": 80},
                "moves": moves,
            }
        )
    return pool, scores


@pytest.mark.parametrize("generation", ALL_GENERATIONS)
class TestSolverFeasibility:
    def test_optimal_with_synthetic_pool(self, generation):
        pool, scores = _make_pool_and_scores(generation)
        params = Params(
            max_overlap=6,
            min_redundancy=0,
            max_same_type_moves=4,
            min_role_types=0,
        )
        model = build_model(pool, scores, params, generation=generation)
        status, team, _, _ = solve_model(model)
        assert status == "Optimal"
        assert len(team) == 6

    def test_scores_cover_all_defending_types(self, generation):
        expected_types = set(types_for_generation(generation))
        pool, scores = _make_pool_and_scores(generation)
        covered = {dt for (_, _, dt) in scores}
        assert covered == expected_types


# ---------------------------------------------------------------------------
# 4. Compiled data schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "compiled_path",
    COMPILED_FILES,
    ids=[p.stem for p in COMPILED_FILES],
)
class TestCompiledDataSchema:
    @pytest.fixture(autouse=True)
    def _load(self, compiled_path):
        self.data = json.loads(compiled_path.read_text())

    def test_top_level_keys(self):
        required = {"version_group", "generation", "label", "unlimited_tms", "pokemon"}
        missing = required - self.data.keys()
        assert not missing, f"Missing top-level keys: {missing}"

    def test_version_group_is_registered(self):
        assert self.data["version_group"] in GAME_PROFILES

    def test_generation_is_supported(self):
        assert self.data["generation"] in SUPPORTED_GENERATIONS

    def test_dataset_generation_is_supported(self):
        gen = dataset_generation(self.data)
        assert gen in SUPPORTED_GENERATIONS

    def test_pokemon_entries_have_required_keys(self):
        for poke in self.data["pokemon"][:5]:
            missing = REQUIRED_POKEMON_KEYS - poke.keys()
            assert not missing, f"{poke['name']}: missing keys {missing}"

    def test_move_entries_have_required_keys(self):
        for poke in self.data["pokemon"][:5]:
            for move in poke["moves"][:3]:
                missing = REQUIRED_MOVE_KEYS - move.keys()
                assert (
                    not missing
                ), f"{poke['name']}/{move['name']}: missing keys {missing}"
