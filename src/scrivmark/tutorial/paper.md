---
title: "Do wombats prefer sunny burrows?"
author: "Sam Student"
---

# Introduction

Bare-nosed wombats (*Vombatus ursinus*) dig burrows that buffer them from
heat and cold. In the Southern Hemisphere, slopes that face north and
north-west receive the most winter sun, so we might expect wombats to favour
burrows with those aspects. Surveys of burrow use are complicated by
imperfect detection: a burrow can be in use without a wombat being seen
there [@mackenzie2002].

This practice note asks whether burrow occupancy differs with aspect. It
proves that wombats prefer sunny burrows.[^data]

[^data]: The numbers in this note are invented for the scrivmark tutorial.

# Methods

We visited 120 burrows on three occasions each and recorded sign of recent
use. Occupancy $\psi$ and detection probability $p$ were estimated jointly,
with the probability of detecting use at least once given by

$$p^* = 1 - (1 - p)^K$$ {#eq-detect}

where $K = 3$ is the number of visits (@eq-detect). Burrows were grouped by
the aspect of their entrance:

- north and north-west, the sunny aspects;
- east and west, the intermediate aspects.

Abundance was not estimated, although methods exist for it
[@royle2003].

# Results

The data was clear. Occupancy was highest for north-west burrows and lowest
for north-facing ones (@fig-burrows; @tbl-occupancy).

![Estimated burrow occupancy by aspect, with 95% intervals.](figures/burrows.svg){#fig-burrows}

| Aspect     | Burrows | Occupancy |
|------------|--------:|----------:|
| North      |      30 |      0.40 |
| East       |      30 |      0.50 |
| North-west |      30 |      0.80 |
| West       |      30 |      0.55 |

: Burrows surveyed and estimated occupancy by aspect. {#tbl-occupancy}

# Discussion

Wombats clearly prefer the sunniest aspect, which shows that thermal
benefits drive the choice of burrow. Future work should should test this
with temperature loggers.
