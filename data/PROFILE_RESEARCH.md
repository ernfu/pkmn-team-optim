# Adding New Generation Profiles

Use this guide when extending `data/profiles.py` for another Pokemon generation or
version group.

## Goal

Keep the runtime shape of `GAME_PROFILES` stable. Each version group profile should
still expose:

- `generation`
- `pokedex`
- `label`
- `unlimited_tms`
- `variable_power_estimates`

## Automatic Overrides from PokeAPI

The compiler in `data/pokedex.py` automatically resolves generation-correct values
at compile time using PokeAPI historical data. No manual overrides are needed for:

- **Pokemon types** — derived from `past_types` on the Pokemon resource.
- **Move types** — derived from `past_values[].type` on the move resource.
- **Move power** — derived from `past_values[].power` on the move resource.
- **Move accuracy** — derived from `past_values[].accuracy` on the move resource.

These cover cases like Fairy-type reclassifications (e.g. Clefairy was Normal before
Gen 6) and historical move power changes (e.g. Tackle was 35 before Gen 5).

## Research Workflow

1. Look up the PokeAPI `version_group` and `pokedex` slugs for the target game.
2. Identify any reusable or unlimited TMs for the optimizer.
3. Verify the `generation` number the game should use for mechanics.

Recommended search targets:

- PokeAPI docs and live resource names for version groups and pokedex ids
- Bulbapedia generation mechanic pages
- Serebii game data pages

Suggested searches:

- `PokeAPI version-group <game name>`
- `PokeAPI pokedex <regional dex name>`
- `Bulbapedia <game name> TM list`

## Data To Gather

For each new version group, collect:

- The exact PokeAPI `version_group` slug.
- The exact PokeAPI `pokedex` slug used for that game's obtainable dex.
- A human-readable `label`.
- The `generation` number the game should inherit rules from.
- Any reusable or effectively unlimited TMs the optimizer should treat as non-scarce.

For each generation profile (only if a new generation number is introduced):

- Variable-power moves that still need a scoring estimate when API power is null.

## Implementation Notes

1. Add a generation entry in `GENERATION_PROFILES` if the generation number is new
   (it only needs `COMMON_BATTLE_PROFILE` unless custom `variable_power_estimates`
   are required).
2. Add the game-specific entry in `VERSION_GROUP_BASES`.
3. Run `python -m data.pokedex compile <version-group>` to verify the compiled output.

## Validation Checklist

After editing:

1. Run the focused tests for `data/pokedex.py`.
2. If a compiled dataset exists for that version group, spot-check:
   - a Pokemon with a known old typing difference (resolved automatically)
   - a move with a known historical power or type difference (resolved automatically)
   - TM availability for a machine that should be unlimited
3. Confirm `get_profile("<version-group>")` still returns all required keys.
