# Optimisation Problem - Gen 3 Pokémon Team Optimizer

A lexicographic **max-min Mixed-Integer Linear Program (MILP)** that selects a 6-Pokémon team maximising worst-case super-effective damage from a **single attacker**, solved with PuLP (HiGHS).

The main goal is to find a team where each Pokémon is a strong multi-type specialist, and the team collectively covers all types with best-in-class attackers.

MILP idea: pick 6 Pokémon and assign each up to 4 moves to maximise the *weakest* best-single-attacker damage across all 17 defending types. Only one Pokémon battles at a time (matching gameplay), so damage against a type is determined by the team's strongest specialist, not the sum of all members. Brute-force over $\binom{73}{6} \approx 7 \times 10^8$ team combinations (73 full evolves, pick 6) with $\sim 20^4$ moveset assignments each is infeasible, so we formulate it as a MILP and let a branch-and-bound solver handle it.

---

## 1 Sets & Indices

| Symbol | Meaning |
|---|---|
| $\mathcal{P}$ | Pool of fully-evolved Pokémon (optionally excluding legendaries). Typically $\|\mathcal{P}\| \approx 73$. |
| $\mathcal{T}$ | The 17 defending types: normal, fire, water, electric, grass, ice, fighting, poison, ground, flying, psychic, bug, rock, ghost, dragon, dark, steel |
| $\mathcal{M}_p$ | Set of attacking moves available to Pokémon $p$ (power > 0). Varies per Pokémon; averages ~22 moves. |
| $\tau(m)$ | Attacking type of move $m$ |
| $\text{types}(p) \subseteq \mathcal{T}$ | Type(s) of Pokémon $p$ |

---

## 2 Pre-computed Score

All $S_{p,m,t}$ values are computed before the solver runs - they become **constants** (coefficients) in the MILP, not decision variables. This keeps the formulation linear.

For every triple $(p, m, t)$ where move $m$ is super-effective against defending type $t$:

$$
S_{p,m,t} = \text{power}_m^{*}  \cdot  \left(\frac{\text{acc}_m}{100}\right)^{\alpha}  \cdot  \text{STAB}_{p,m}  \cdot  2.0  \cdot  \text{stat}_{p,m}  \cdot  \text{speedFactor}_p  \cdot  \text{recoilFactor}_m  \cdot  \text{priorityFactor}_m
$$

| Term | Value | Notes |
|---|---|---|
| $\text{power}_m^{*}$ | Base power | Proportional to the numerator of the Gen 3 damage formula: $\lfloor\frac{(2L/5+2) \cdot \text{Atk} \cdot \text{Power}}{50 \cdot \text{Def}} + 2\rfloor \cdot \text{Modifier}$. Halved for recharge moves like Hyper Beam)  |
| $(\text{acc}/100)^\alpha$ | Accuracy factor, $\alpha = 2.0$ default | Converts raw damage to *expected* damage per attempt. The exponent $\alpha > 1$ penalises low-accuracy moves more than a straight probability would - a design choice to reflect that missing matters more than the expected-value calculation suggests (tempo loss, wasted turn). |
| $\text{STAB}_{p,m}$ | 1.5 or 1.0 | Same-type attack bonus, directly from the game formula |
| $2.0$ | Constant | Super-effective multiplier. Only SE triples are stored, so this is always 2.0. |
| $\text{stat}_{p,m}$ | Base Atk or Sp.Atk | The other half of the damage numerator. Multiplying $\text{stat} \times \text{power}$ is a valid proxy for ranking offensive output because the terms we drop - level factor $(2L/5+2)$, division by $50 \cdot \text{Def}$, the $+2$ floor constant - are either shared across all candidates or unknown (defender's defense). |
| $\text{speedFactor}$ | See notes | Linear speed bonus: $1 + \beta \cdot (v_p - v_{\min}) / (v_{\max} - v_{\min})$ where $v$ is base speed. $\beta$ (`speed_bonus`, default 0.25) is the max bonus for the fastest Pokémon in the pool. Slowest gets 1.0×, fastest gets $(1+\beta)\times$. |
| $\text{recoilFactor}_m$ | $1 - \text{recoilPct}$ | Penalises self-damaging moves proportionally to recoil. Double-Edge (33% recoil) gets 0.67×, Take-Down and Submission (25% recoil) get 0.75×. Non-recoil moves get 1.0×. |
| $\text{priorityFactor}_m$ | $\gamma$ or 1.0 | Penalises negative-priority moves like Focus Punch which fail if the user is hit before attacking. $\gamma$ (`low_priority_factor`, default 0.3) applies to these moves; all others get 1.0×. |

$S_{p,m,t} = 0$ whenever $m$ is **not** super-effective against $t$. Non-SE moves (neutral, resisted, immune) are invisible to the optimiser - it only cares about SE damage.

### Gen 3 Physical / Special Split

In Gen 3 (unlike Gen 4+), physical vs. special is determined by the **move's type**, not the move itself:

- **Physical:** Normal, Fighting, Flying, Poison, Ground, Rock, Bug, Ghost, Steel
- **Special:** Fire, Water, Electric, Grass, Ice, Psychic, Dragon, Dark

This means a Pokémon with high Atk but low Sp.Atk gets poor scores for Fire/Water/etc. moves even if those moves have high base power - matching the actual in-game damage.

---

## 3 Decision Variables

| Variable | Type | Count | Purpose |
|---|---|---|---|
| $x_p$ | Binary | $\|\mathcal{P}\| \approx 73$ | 1 if Pokémon $p$ is on the team |
| $y_{p,m}$ | Binary | $\sum_p \|\mathcal{M}_p\| \approx 1600$ | 1 if move $m$ is in $p$'s moveset |
| $u_{p,t}$ | Binary | ~500 (one per SE-reachable pair) | 1 if Pokémon $p$ is the designated attacker against type $t$ (see §5.5) |
| $z$ | Continuous | 1 | Auxiliary: the worst-case single-attacker damage we're maximising |
| $w_{p,m,t}$ | Continuous $[0, 1]$ | ~4000 (one per SE triple) | 1 if Pokémon $p$ uses move $m$ against defending type $t$ (see §5.3–5.4) |

The $y$ and $u$ variables are binary; $y$ dominates branching (~1600 vars) while $u$ adds ~500 more for the attacker-assignment. The $w$ variables are continuous and don't add branching complexity.

---

## 4 Objective

The solver now uses a **lexicographic** objective sequence instead of a weighted tie-break:

### Stage 1: Maximise worst-case coverage

$$
\max \quad z
$$

- **$z$** - the worst-case single-attacker damage. This remains the primary optimisation goal.

### Stage 2: Minimise duplicate attacking types

For each Pokémon $p$ and attacking type $\tau$, define the selected move count:

$$
n_{p,\tau} = \sum_{\substack{m \in \mathcal{M}_p \\ \tau(m) = \tau}} y_{p,m}
$$

Introduce within-Pokémon duplicate variables:

$$
d^{\text{within}}_{p,\tau} \ge 0
$$

$$
d^{\text{within}}_{p,\tau} \ge n_{p,\tau} - 1 \qquad \forall\, p \in \mathcal{P},\, \tau \in \mathcal{T}
$$

For each attacking type $\tau$, define the full-team usage count:

$$
N_{\tau} = \sum_{p \in \mathcal{P}} \sum_{\substack{m \in \mathcal{M}_p \\ \tau(m) = \tau}} y_{p,m}
$$

Introduce team-wide duplicate variables:

$$
d^{\text{team}}_{\tau} \ge 0
$$

$$
d^{\text{team}}_{\tau} \ge N_{\tau} - 1 \qquad \forall\, \tau \in \mathcal{T}
$$

The diversity stage minimises:

$$
\min \quad
\sum_{p \in \mathcal{P}} \sum_{\tau \in \mathcal{T}} d^{\text{within}}_{p,\tau}
\;+\;
\sum_{\tau \in \mathcal{T}} d^{\text{team}}_{\tau}
$$

This penalises excess repeats beyond the first copy of an attacking type, both within a single moveset and across the whole team, without introducing a user-facing weight.

### Stage 3: Maximise total firepower

After fixing the optimal values from Stages 1 and 2, the solver maximises:

$$
\max \quad \sum_{t \in \mathcal{T}} \sum_{p \in \mathcal{P}} \sum_{m \in \mathcal{M}_p} S_{p,m,t} \, y_{p,m}
$$

This is a pure tie-break among equally strong and equally diverse teams, so there is no need for an $\varepsilon$-style magic coefficient.

### Equivalence to max-min-max

The natural formulation is $\max \min_{t} \max_{p} d_{p,t}$ — maximise the worst-case damage when only the best single attacker fights each type. Both $\min$ and $\max$ are non-linear.

The outer $\min$ is linearised with the standard epigraph trick: introduce $z \in \mathbb{R}$ and add one constraint per type:

$$
z \leq \text{damage}(t) \qquad \forall\, t \in \mathcal{T}
$$

The inner $\max_p$ is linearised via binary attacker-assignment variables $u_{p,t}$ (see §5.5): only one Pokémon's damage contributes per type, so `damage(t)` effectively equals the designated attacker's best move score.

At optimality, $z^* = \min_t \max_p d_{p,t}$.

---

## 5 Constraints

### 5.1 Team Size

$$
\sum_{p \in \mathcal{P}} x_p = 6
$$

### 5.2 Move–Pokémon Coupling

Each Pokémon carries at most 4 moves, and only if selected:

$$
\sum_{m \in \mathcal{M}_p} y_{p,m} \leq 4\, x_p \qquad \forall\, p \in \mathcal{P}
$$

$$
y_{p,m} \leq x_p \qquad \forall\, p \in \mathcal{P},\ m \in \mathcal{M}_p
$$

The first constraint is a *big-M* style coupling: if $x_p = 0$ (Pokémon not selected), all its $y$ variables are forced to 0. The second set is redundant given the first but tightens the LP relaxation - without it, fractional $x_p$ values can activate more $y$ variables than they should, widening the gap.

### 5.3 Min Damage (defines z)

For every defending type $t$, the designated attacker's damage must be at least $z$:

$$
z  \leq  \sum_{p \in \mathcal{P}} \sum_{m \in \mathcal{M}_p} S_{p,m,t} \cdot w_{p,m,t} \qquad \forall\, t \in \mathcal{T}
$$

Structurally this is the same sum as before, but the attacker-assignment constraints (§5.5) force $w_{p,m,t} = 0$ for every Pokémon except the one designated attacker. The effective value is therefore $\max_p \max_m S_{p,m,t} \cdot y_{p,m}$ — the best single Pokémon's best move. This matches gameplay where only one Pokémon battles at a time.

### 5.4 Best Move Per Matchup (action-selection model)

For each SE triple $(p, m, t)$ where $S_{p,m,t} > 0$, introduce a continuous variable $w_{p,m,t} \in [0, 1]$ representing whether Pokémon $p$ uses move $m$ against defending type $t$.

A move can only be used if it is in the Pokémon's selected moveset:

$$
w_{p,m,t} \leq y_{p,m} \qquad \forall\, p,\, m,\, t \text{ with } S_{p,m,t} > 0
$$

A move can only be used if this Pokémon is the designated attacker against type $t$ (see §5.5):

$$
w_{p,m,t} \leq u_{p,t} \qquad \forall\, p,\, m,\, t \text{ with } S_{p,m,t} > 0
$$

Each Pokémon uses at most one move per matchup:

$$
\sum_{\substack{m \in \mathcal{M}_p \\ S_{p,m,t} > 0}} w_{p,m,t} \leq 1 \qquad \forall\, p \in \mathcal{P},\, t \in \mathcal{T}
$$

Since $z$ is being maximised and each type constraint (§5.3) benefits from larger $w$ values, the solver automatically sets $w_{p,m^*,t} = 1$ for the highest-scoring selected move $m^*$ of the designated attacker and $w_{p,m,t} = 0$ for the rest (including all non-designated Pokémon, forced to 0 by the $u$ constraint). This gives exactly $d_{p,t} = \max_{m} \{S_{p,m,t} \cdot y_{p,m}\}$ for the chosen attacker without needing $w$ to be binary — the LP relaxation is exact because "pick the best of $N$" is solved greedily.

Note that the same Pokémon can use different moves against different defending types (e.g., Blaziken uses Brick Break vs Steel but Blaze Kick vs Grass). The 4-move constraint (§5.2) still matters because it determines which moves are available across all matchups.

### 5.5 Single-Attacker Assignment

Only one Pokémon contributes damage per defending type, matching gameplay where a single Pokémon battles at a time. For each $(p, t)$ pair where $p$ has at least one SE move against $t$, introduce a binary variable $u_{p,t}$.

At most one Pokémon is the designated attacker per type:

$$
\sum_{\substack{p \in \mathcal{P} \\ \exists\, m: S_{p,m,t} > 0}} u_{p,t} \leq 1 \qquad \forall\, t \in \mathcal{T}
$$

The attacker must be on the team:

$$
u_{p,t} \leq x_p \qquad \forall\, p,\, t
$$

We use $\leq 1$ rather than $= 1$ to avoid infeasibility when no selected Pokémon has SE coverage against a type. At optimality the solver always picks exactly one attacker (since maximising $z$ wants damage as high as possible).

The SE redundancy constraint (§5.9) ensures backup attackers exist even though only one contributes to the objective. The final firepower stage then prefers stronger backups among teams that already tie on coverage and diversity.

### 5.6 Move-Type Diversity

Each Pokémon may carry at most $c$ moves of the same attacking type:

$$
\sum_{\substack{m \in \mathcal{M}_p \\ \tau(m) = \tau_0}} y_{p,m} \leq c \qquad \forall\, p \in \mathcal{P},\, \tau_0 \in \mathcal{T}
$$

Default $c = 2$. Under the single-attacker model (§5.5) the action-selection variables $w$ already pick the best move per matchup, so a 2nd move of the same type can never outperform a diverse move. This constraint forces the solver to fill remaining slots with coverage for other types rather than redundant same-type moves. At $c = 1$ every move slot covers a distinct type; at $c = 4$ the constraint is inactive.

### 5.7 Team-Wide Move-Type Diversity

In addition to the per-Pokémon cap above, the solver also discourages repeating the same attacking type across the whole team. For each attacking type $\tau$:

$$
N_{\tau} = \sum_{p \in \mathcal{P}} \sum_{\substack{m \in \mathcal{M}_p \\ \tau(m) = \tau}} y_{p,m}
$$

$$
d^{\text{team}}_{\tau} \ge N_{\tau} - 1, \qquad d^{\text{team}}_{\tau} \ge 0
$$

These variables are not hard constraints by themselves; they are minimised in Objective Stage 2. A team can still repeat a type when coverage demands it, but the optimiser now prefers spreading attacking types when coverage is otherwise tied.

### 5.8 Type Overlap Cap

At most $n$ Pokémon on the team may share any single type:

$$
\sum_{\substack{p \in \mathcal{P} \\ t \in \text{types}(p)}} x_p  \leq  n \qquad \forall\, t \in \mathcal{T}
$$

Default $n = 1$.

### 5.9 Super-Effective Redundancy

At least $k$ (Pokémon, move) pairs with a super-effective move against every defending type:

$$
\sum_{p \in \mathcal{P}} \sum_{\substack{m \in \mathcal{M}_p \\ S_{p,m,t} > 0}} y_{p,m}  \geq  k \qquad \forall\, t \in \mathcal{T}
$$

Default $k = 2$.

### 5.10 Single-Use TM Uniqueness

For each single-use TM move (TMs not in the unlimited set), at most one Pokémon may learn it:

$$
\sum_{p \in \text{users}(m_{\text{TM}})} y_{p,\, m_{\text{TM}}}  \leq  1 \qquad \forall\ m_{\text{TM}} \in \text{SingleUseTMs}
$$

Unlimited TMs (purchasable repeatedly in FRLG): Ice Beam, Thunderbolt, Flamethrower, Iron Tail, Hyper Beam, Dig, Brick Break, Rest, Secret Power, Attract, Roar.

### 5.11 User Constraints

**Lock Pokémon** - Force $p$ onto the team:

$$x_p = 1$$

**Lock Move** - Force move $m$ onto locked Pokémon $p$:

$$y_{p,m} = 1$$

**Must-Have Move** - At least one team member carries move $m$:

$$
\sum_{\substack{p \in \mathcal{P} \\ m \in \mathcal{M}_p}} y_{p,m}  \geq  1
$$

**Must-Have Type** - At least one team member is of type $t$:

$$
\sum_{\substack{p \in \mathcal{P} \\ t \in \text{types}(p)}} x_p  \geq  1
$$

---

## 6 Solver

The MILP is solved with **PuLP** using the **HiGHS** solver (`pip install highspy`). HiGHS is a modern open-source solver significantly faster than CBC on MILPs.

### Problem Size

After PuLP builds the model and HiGHS preprocesses it:

- ~10,500 rows (constraints), ~6,100 columns (variables), ~1,500 binary
- The ~500 binary $u_{p,t}$ attacker-assignment variables add branching complexity alongside the ~985 $x$ and $y$ variables
- The ~4000 $w_{p,m,t}$ variables are continuous $[0,1]$ and don't add branching complexity
- The ~4000 $w \leq u$ linking constraints are the main row-count increase vs. the sum-based formulation

---

## 7 Parameter Summary

| Parameter | Symbol | Default | Effect on solve |
|---|---|---|---|
| `max_overlap` | $n$ | 1 | How many team members can share a type. Lower values tighten the feasible region - can make the problem infeasible if too restrictive. |
| `min_redundancy` | $k$ | 2 | At least $k$ Pokémon must have a SE move against each enemy type. Higher values add harder constraints; $k \geq 3$ often infeasible. |
| `max_same_type_moves` | $c$ | 2 | Max moves of the same attacking type per Pokémon. At 1, every slot must be a different type; at 4, no restriction. Forces move diversity. |
| `acc_exponent` | $\alpha$ | 2.0 | Accuracy penalty: mult = $(\text{acc}/100)^\alpha$. At 2.0, 85% acc → 0.72×, 70% acc → 0.49×. Only affects pre-computed scores, not the MILP structure. |
| `speed_bonus` | $\beta$ | 0.25 | Bonus for fast Pokémon. At 0.25, the fastest gets $1.25\times$ damage, the slowest gets $1.0\times$. Linear interpolation. |
| `low_priority_factor` | $\gamma$ | 0.3 | Multiplier for negative-priority moves (e.g., Focus Punch). 0.3 = 30% credit. Not exposed in CLI/UI. |

### Known Limitations

- **Single-type defenders only.** The model treats each of the 17 types independently. Dual-type matchups (e.g., 4× against Ground/Flying, or immunity from Normal/Ghost) are not modelled.
- **No immunities.** A score of 0 means "not super-effective," but the model doesn't distinguish "neutral" from "immune."
- **No defensive stats.** HP, Def, Sp.Def are ignored - the model assumes every Pokémon survives long enough to attack.
- **Speed is a proxy.** The linear bonus approximates the value of moving first but doesn't model actual speed tiers.
