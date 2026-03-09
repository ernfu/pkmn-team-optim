# Optimisation Problem - Gen 3 Pokémon Team Optimizer

A regularised **max-min Mixed-Integer Linear Program (MILP)** that selects a 6-Pokémon team maximising worst-case super-effective damage, solved with PuLP (HiGHS).

The core idea: pick 6 Pokémon and assign each up to 4 moves to maximise the *weakest* super-effective damage across all 17 defending types. This is a combinatorial optimisation - brute-force over $\binom{73}{6} \approx 7 \times 10^8$ team combinations with $\sim 20^4$ moveset assignments each is infeasible, so we formulate it as a MILP and let a branch-and-bound solver handle it.

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
S_{p,m,t} = \text{power}_m^{*} \;\cdot\; \left(\frac{\text{acc}_m}{100}\right)^{\!\alpha} \;\cdot\; \text{STAB}_{p,m} \;\cdot\; 2.0 \;\cdot\; \text{stat}_{p,m} \;\cdot\; \text{speed\_factor}_p \;\cdot\; \text{recoil\_factor}_m \;\cdot\; \text{priority\_factor}_m
$$

| Term | Value | Notes |
|---|---|---|
| $\text{power}_m^{*}$ | Base power | Proportional to the numerator of the Gen 3 damage formula: $\lfloor\frac{(2L/5+2) \cdot \text{Atk} \cdot \text{Power}}{50 \cdot \text{Def}} + 2\rfloor \cdot \text{Modifier}$. Halved for recharge moves like Hyper Beam)  |
| $(\text{acc}/100)^\alpha$ | Accuracy factor, $\alpha = 2.0$ default | Converts raw damage to *expected* damage per attempt. The exponent $\alpha > 1$ penalises low-accuracy moves more than a straight probability would - a design choice to reflect that missing matters more than the expected-value calculation suggests (tempo loss, wasted turn). |
| $\text{STAB}_{p,m}$ | 1.5 or 1.0 | Same-type attack bonus, directly from the game formula |
| $2.0$ | Constant | Super-effective multiplier. Only SE triples are stored, so this is always 2.0. |
| $\text{stat}_{p,m}$ | Base Atk or Sp.Atk | The other half of the damage numerator. Multiplying $\text{stat} \times \text{power}$ is a valid proxy for ranking offensive output because the terms we drop - level factor $(2L/5+2)$, division by $50 \cdot \text{Def}$, the $+2$ floor constant - are either shared across all candidates or unknown (defender's defense). |
| $\text{speed\_factor}_p$ | $1 + \beta \cdot \frac{\text{speed}_p - \text{speed}_{\min}}{\text{speed}_{\max} - \text{speed}_{\min}}$ | Linear speed bonus. $\beta$ (`speed_bonus`, default 0.1) is the max bonus for the fastest Pokémon in the pool. Slowest gets 1.0×, fastest gets $(1+\beta)\times$. |
| $\text{recoil\_factor}_m$ | $1 - \text{recoil\_pct}$ | Penalises self-damaging moves proportionally to recoil. Double-Edge (33% recoil) gets 0.67×, Take-Down and Submission (25% recoil) get 0.75×. Non-recoil moves get 1.0×. |
| $\text{priority\_factor}_m$ | $\gamma$ or 1.0 | Penalises negative-priority moves like Focus Punch which fail if the user is hit before attacking. $\gamma$ (`low_priority_factor`, default 0.3) applies to these moves; all others get 1.0×. |

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
| $z$ | Continuous | 1 | Auxiliary: the worst-case damage value we're maximising |
| $f_{p,m}$ | Continuous $[0, 1]$ | varies | 1 if move $m$ gets full credit (vs. discounted; see §5.4) |

The $y$ variables dominate - ~1600 binary variables is modest for a MILP but enough that the LP relaxation gap matters for solve time.

---

## 4 Objective

$$
\max \quad z \;+\; \varepsilon \sum_{t \in \mathcal{T}} \sum_{p \in \mathcal{P}} \sum_{m \in \mathcal{M}_p} S_{p,m,t} \; y_{p,m}
$$

where $\varepsilon = 10^{-4}$.



- **$z$** - the worst-case damage. Maximising this directly is the primary goal.
- **$\varepsilon \cdot \text{total\_power}$** - a regularisation/tie-breaker. Many teams can achieve the same $z^*$; this selects the one with the highest total SE firepower. The small $\varepsilon$ ensures this never overrides a genuine improvement to worst-case damage (since individual $S$ values are in the thousands, $\varepsilon \cdot \text{total} \ll z$ for any meaningful difference in $z$).

### Equivalence to max-min

The natural formulation is $\max \min_{t} \text{damage}(t)$, but $\min(\cdot)$ is non-linear. The standard epigraph trick replaces it: introduce $z \in \mathbb{R}$ and add one constraint per type:

$$
z \leq \text{damage}(t) \qquad \forall\, t \in \mathcal{T}
$$

Since $z$ is being maximised, the solver pushes it up until it's tight against the binding (weakest) type. At optimality, $z^* = \min_t \text{damage}(t)$.

This is a standard LP/MILP pattern - any max-min or min-max over a finite set can be linearised this way. The regularisation term doesn't affect the equivalence since $\varepsilon$ is small enough that it can't compensate for a unit decrease in $z$.

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
y_{p,m} \leq x_p \qquad \forall\, p \in \mathcal{P},\; m \in \mathcal{M}_p
$$

The first constraint is a *big-M* style coupling: if $x_p = 0$ (Pokémon not selected), all its $y$ variables are forced to 0. The second set is redundant given the first but tightens the LP relaxation - without it, fractional $x_p$ values can activate more $y$ variables than they should, widening the gap.

### 5.3 Min Damage (defines z)

For every defending type $t$, the team's effective damage must be at least $z$. The damage contribution of each move depends on whether it belongs to a duplicate-type group (§5.4):

$$
z \;\leq\; \sum_{p \in \mathcal{P}} \sum_{m \in \mathcal{M}_p} c_{p,m,t} \qquad \forall\, t \in \mathcal{T}
$$

where the per-move contribution $c_{p,m,t}$ is:

$$
c_{p,m,t} = \begin{cases}
  \delta \cdot S_{p,m,t} \cdot y_{p,m} \;+\; (1-\delta) \cdot S_{p,m,t} \cdot f_{p,m}
    & \text{if } m \in G_{p,\tau(m)},\; |G_{p,\tau(m)}| \geq 2 \\[4pt]
  S_{p,m,t} \cdot y_{p,m}
    & \text{otherwise}
\end{cases}
$$

Moves with a unique attacking type on their Pokémon always contribute at full value. Moves that share an attacking type with another candidate are split into a guaranteed base ($\delta$ fraction) and a bonus ($(1-\delta)$ fraction) that only the full-credit move receives. See §5.4.

### 5.4 Move Type Diversity (full-credit model)

For each Pokémon $p$ and each attacking type $\tau$ with two or more candidate moves, let $G_{p,\tau} \subseteq \mathcal{M}_p$ be the group. Introduce a continuous variable $f_{p,m} \in [0,1]$ for each move in the group:

$$
f_{p,m} \leq y_{p,m} \qquad \forall\, m \in G_{p,\tau}
$$

$$
\sum_{m \in G_{p,\tau}} f_{p,m} \leq 1
$$

At most one move per type group gets full credit. Every selected move in the group contributes at least $\delta \cdot S_{p,m,t}$ (the base fraction); the single full-credit move contributes the remaining $(1-\delta) \cdot S_{p,m,t}$ on top.

Since we are maximising, the solver sets $f_{p,m} = 1$ for whichever move in the group scores highest against the binding (weakest) damage constraint. This is exact — unlike an average-based penalty, it correctly discounts the specific second move rather than approximating.

$\delta$ is the `duplicate_type_discount` parameter (default 0.5). At $\delta = 0$ the second same-type move contributes nothing (equivalent to a hard ban); at $\delta = 1$ no penalty is applied.

### 5.5 Type Overlap Cap

At most $n$ Pokémon on the team may share any single type:

$$
\sum_{\substack{p \in \mathcal{P} \\ t \in \text{types}(p)}} x_p \;\leq\; n \qquad \forall\, t \in \mathcal{T}
$$

Default $n = 2$.

### 5.6 Super-Effective Redundancy

At least $k$ (Pokémon, move) pairs with a super-effective move against every defending type:

$$
\sum_{p \in \mathcal{P}} \sum_{\substack{m \in \mathcal{M}_p \\ S_{p,m,t} > 0}} y_{p,m} \;\geq\; k \qquad \forall\, t \in \mathcal{T}
$$

Default $k = 2$.

### 5.7 Single-Use TM Uniqueness

For each single-use TM move (TMs not in the unlimited set), at most one Pokémon may learn it:

$$
\sum_{p \in \text{users}(m_{\text{TM}})} y_{p,\, m_{\text{TM}}} \;\leq\; 1 \qquad \forall\; m_{\text{TM}} \in \text{SingleUseTMs}
$$

Unlimited TMs (purchasable repeatedly in FRLG): Ice Beam, Thunderbolt, Flamethrower, Iron Tail, Hyper Beam, Dig, Brick Break, Rest, Secret Power, Attract, Roar.

### 5.8 User Constraints

**Lock Pokémon** - Force $p$ onto the team:

$$x_p = 1$$

**Lock Move** - Force move $m$ onto locked Pokémon $p$:

$$y_{p,m} = 1$$

**Must-Have Move** - At least one team member carries move $m$:

$$
\sum_{\substack{p \in \mathcal{P} \\ m \in \mathcal{M}_p}} y_{p,m} \;\geq\; 1
$$

**Must-Have Type** - At least one team member is of type $t$:

$$
\sum_{\substack{p \in \mathcal{P} \\ t \in \text{types}(p)}} x_p \;\geq\; 1
$$

---

## 6 Solver

The MILP is solved with **PuLP** using the **HiGHS** solver (`pip install highspy`). HiGHS is a modern open-source solver significantly faster than CBC on MILPs.

### Problem Size

After PuLP builds the model and HiGHS preprocesses it:

- ~1750 rows (constraints), ~1670 columns (variables), ~985 binary
- The $f_{p,m}$ variables are continuous but bounded $[0,1]$ and coupled to binary $y_{p,m}$ variables, so they don't add branching complexity
- LP relaxation solves instantly; the gap between LP relaxation and best integer solution is typically ~10%

---

## 7 Parameter Summary

| Parameter | Symbol | Default | Effect on solve |
|---|---|---|---|
| `max_overlap` | $n$ | 2 | Caps same-type Pokémon. Lower values tighten the feasible region - can make the problem infeasible if too restrictive. |
| `min_redundancy` | $k$ | 2 | Requires $k$ SE (Pokémon, move) pairs per defending type. Higher values add harder constraints; $k \geq 3$ often infeasible. |
| `acc_exponent` | $\alpha$ | 2.0 | Accuracy penalty harshness. Only affects pre-computed scores, not the MILP structure. |
| `duplicate_type_discount` | $\delta$ | 0.5 | Credit for a 2nd same-type move (0–1). At 0 the full-credit constraint becomes a hard ban; at 1 the $f$ variables are omitted entirely. |
| `speed_bonus` | $\beta$ | 0.1 | Fastest Pokémon gets $(1+\beta)\times$ effective power, slowest gets $1.0\times$. Linear interpolation. |
| `low_priority_factor` | $\gamma$ | 0.3 | Multiplier for negative-priority moves (e.g., Focus Punch). 0.3 = 30% credit. Set to 1.0 to disable. |
| Regularisation | $\varepsilon$ | $10^{-4}$ | Tie-break weight. Must be small enough that $\varepsilon \cdot \text{total\_power} < 1$ unit of $z$ improvement. |

### Known Limitations

- **Single-type defenders only.** The model treats each of the 17 types independently. Dual-type matchups (e.g., 4× against Ground/Flying, or immunity from Normal/Ghost) are not modelled.
- **No immunities.** A score of 0 means "not super-effective," but the model doesn't distinguish "neutral" from "immune."
- **No defensive stats.** HP, Def, Sp.Def are ignored - the model assumes every Pokémon survives long enough to attack.
- **Speed is a proxy.** The linear bonus approximates the value of moving first but doesn't model actual speed tiers.
