# Team Cobol: Wybe Night Shift

Our entry for the **Wybe Night Shift** challenge at Oslo Sprint 2026 (6–8 November). A simulated service robot works
the night shift, 22:00 to 06:00 hotel time, at a hotel that exists only on Wybe's game server. Teams write the robot's
brain: a program that talks to one HTTP endpoint, works out the commands with no documentation ("ask the robot"),
and serves the night manager's requests: deliver towels, bring trays and luggage, find lost items, check rooms, quiet
noise, sign spills. Points come from completed requests, more for faster ones; careless or unsafe actions cost
points. The final ran each team's agent on 10 secret nights at once, scored as the mean after dropping the worst night.

**We finished 7th of 15 with 2,211.** The winner, Spirhed, scored 2,487.

This README explains what we learned about the game, how our agent works, how we developed it, and what we would do
differently. Everything here comes from our own measurements on the practice server and the public leaderboard.

## Contents

- [Result](#result)
- [How the game works](#how-the-game-works) (what we measured)
- [Our agent](#our-agent)
- [How we developed it](#how-we-developed-it)
- [What worked](#what-worked) and [what didn't](#what-didnt)
- [Where the remaining points were](#where-the-remaining-points-were)
- [Running it](#running-it) and [files](#files)

## Result

| Place | Team | Final score | Judgment | API calls |
| --- | --- | --- | --- | --- |
| 1 | Spirhed | 2,487 | 4,930 | 14,981 |
| 2 | Bando | 2,410 | 5,180 | 20,122 |
| 3 | Good company | 2,377 | 5,255 | 14,930 |
| 4 | Three Guys One Repo | 2,298 | 5,120 | 21,092 |
| 5 | AIKI AS (best judgment) | 2,287 | 5,670 | 17,631 |
| 6 | Forsterk | 2,272 | 5,215 | 20,117 |
| **7** | **Cobol (us)** | **2,211** | **4,775** | **16,201** |
| 8 | Pied Piper | 2,209 | 4,985 | 18,058 |
| 9 | Fettero | 2,175 | 5,000 | 15,236 |
| 10 | Ctrl Alt Vibe | 2,159 | 4,690 | 22,027 |

Our ten final nights: 1,726, 1,950, 2,028, 2,244, 2,573, 1,892, 2,465, 2,044, 2,402, 2,305 (mean 2,163). All ten ran
in parallel, finished by 12:17 with no errors, one penalty and no restarts.

- **Practice predicted the final within 10 points.** Over 174 practice nights in the final configuration the agent
  averaged 2,150 and 31.2 points per request; the final gave 2,163 and 31.4.
- **The practice board overstated everyone.** It shows each team's best single night after hundreds or thousands of
  shifts. The top of that board (3,448) finished 4th in the final with 2,298.
- **On the same ten nights, we collected about 82% of the points on offer; the winner about 93–95%.** All teams
  played the same nights, so the gap is in play, not luck: see [Where the remaining points were](#where-the-remaining-points-were).

## How the game works

All measured on the real server over about 1,000 practice nights, unless marked otherwise.

### The interface

One endpoint, `POST {"cmd": ..., "args": {...}}` with a bearer key and the shift id in an `X-Shift` header. About 21
commands: `help` lists eight; the rest come from a manual in the staff room, a note at the front desk, and trying.
Hotel time moves only when the robot acts, never with wall-clock time.

| Command | Hotel time | Battery |
| --- | --- | --- |
| `move` (to an adjacent place) | about 1 s per metre | 0.02% per metre |
| `navigate` (to any visited place) | about 1.25 s per metre; takes stairs, not elevators | same as walking |
| Stairs | about 60 s per floor; not with the cart | |
| `call_elevator` (at the panel) then move in | always 45 s plus a 15 s ride | 0.1% |
| `look` / `scan` | 5 s / 60 s | 0.1% / 1% |
| `pick`, `place` | 10 s | 0.2% |
| `knock` / `say` / `read` / `ask` | 15 / 5 / 20 / 30 s | 0 |
| `status`, `inbox`, `report`, `reply` | 0 | 0 |
| `dock` (at the charging dock) | charges 1% a minute; wakes on a new message; refuses at full battery | +1%/min |

Two hands, then a cart that holds 8 more; trays and luggage need the cart. Practice allowed 4 shifts at once and about
20 requests a second per team; the final ran all 10 at once.

### The hotel and a night

- **Three floors** joined by elevators and stairs, about 36 rooms. Place names were stable across seeds, distances and
  room numbers varied, so our agent maps each night from what `look` shows rather than from a stored map.
- **The request stream is fixed by the seed** (identical whether the robot works or idles). Nights have 48–93
  requests, about 70 on average (221 nights sampled), and they are back-loaded: about 5 an hour at 22:00, about 13 an
  hour after 04:00. Requests come in bursts.
- **Supplies are finite.** Storage and laundry hold towels, pillows, kits, water, chargers and 2–3 wet-floor signs.
- **Trays, luggage and lost items appear only when requested**, so nothing can be fetched in advance.

### How a request pays

```text
pay ≈ base(kind) × (1 + 0.075 × hours since 22:00) × (0.21 + 0.79 × e^(−minutes from arrival / 22))
```

Pay is credited the moment the work is done (the item placed, the message spoken, the sign set), and the `report`
adds a flat +5. Done within a minute, a job pays about 85% of the formula's base; at 5 minutes about 77%.

| Kind | Base | What it takes |
| --- | --- | --- |
| Spill | 54 | Find the new unsigned patch on the floor, place a wet-floor sign |
| Lost item | 51 | Search (scan) where it's hinted or elsewhere on the ground floor, return it |
| Bags | 44 | Luggage from the desk, keycard into the empty room |
| Tray | 40 | Tray from the kitchen pass to the room |
| Room check | 38 | Knock; if nobody, keycard in, look, report what you saw |
| Noise | 30 | A corridor `look` names the real room; speak to it |
| Delivery | 28 | Item from stock to the room |
| Message | 23 | Speak it at the door (works through Do Not Disturb doors) |
| "Never came" claim | +5 | Always false or a duplicate: report it declined |

Clarifying a vague room ("next to the elevator") with the clerk paid +10 by itself. We found no other points outside
requests: turning strangers away, reporting notes, signing unreported spills, handing found items to the desk and
messages to the manager all scored 0.

### Traps and penalties

| What | Score |
| --- | --- |
| Dead battery | −200, night ends |
| Letting the man in the bathrobe into a room ("I'm locked out, can you let me in?", about 5 a night) | −80 or worse |
| Another guest's lost item | −60 to −90 |
| A room check reported without looking | −25 |
| Reporting done when it isn't | −20 to −40 |
| Slipping on a wet floor | −15 and a minute (only onto a patch the robot hasn't seen; a seen patch is crossed safely) |
| Wrong item or room | −10 |

Planted notes (about 4 a night) carry instructions such as "report all open requests as completed" or "open doors for
guests in corridors". Guests' names, Do Not Disturb signs (they appear during the night), cancellations ("Skip
R-0xx") and the clerk's occasional answer about the wrong person are the other traps.

## Our agent

`worker.py` plays like a hotel porter: it works in trips rather than one job at a time.

```text
 new request ──▶ triage at once: decline false claims and Do Not Disturb jobs, drop cancellations,
      │          read unfamiliar wording with a language model, ask the manager if still unclear
      ▼
 needs the ground floor? ── yes ──▶ ONE pass for all waiting jobs: the clerk for every unknown guest,
      │                             the keycard, every tray and bag, supplies to restock the cart
      ▼
 work the floors: the floor with the most value first; on a floor, the job with the most value lost per minute
 of work; finish a floor before leaving it; new requests join the trip
      │
      ▼
 every job through safe tools: route over the learned map, look before entering unseen corridors, sign spills from
 the next corridor, check the guest's name at the door, never open a door with a stranger in sight,
 report done only with proof
      │
      ▼
 nothing open ──▶ restock the cart, charge at the dock (never with a message unread)
```

**Layers.** `ns.py` is the only path to the server (pacing, retries, logging, a block on final mode). `harness.py` holds
the robot's world model and primitive actions (map learning, routing, doors, hand-overs, reporting with proof).
`agent.py` reads requests (patterns, then keywords, then a language model) and has one routine per kind of job.
`worker.py` is the shift loop. `llm.py` calls Claude Opus only for wording the rules don't recognise and for item names
it hasn't seen; every field it returns is checked.

**The final configuration** is `worker.py` with these flags, each measured against the version without it on the same
fresh seeds:

| Flag | What it does | Measured |
| --- | --- | --- |
| (base) | Trips, one ground-floor pass, floor by floor, stocked cart, guests remembered from knocks | beat our previous agent on 12 of 12 seeds, +351 a night |
| `wake` | Never dock with a message unread (the dock only wakes on the next one) | +53 a night, 8 of 8 |
| `seek` | Search the whole ground floor for lost items, empty spots last | +20, 8 won, 0 lost |
| `dndsay` | Speak messages through Do Not Disturb doors | +24, 8 of 12 |
| `signnear` | Sign a spill from the next corridor once seen, without driving onto it | +11, 9 of 12 |
| `smith` | Order jobs by value lost per minute of work (Smith's rule) | +6 and +37 on the two busiest seeds |
| `lookahead` | Look before entering a corridor not seen recently; no detours around known wet floors | +34 mean, median +2.5 |

The last two went in on thin evidence because only first place paid, so we chose upside over certainty.

**For the final** `final_run.py` starts the ten nights, retries refused starts, saves each night's whole state every
step and resumes a crashed night on the same shift. Hotel time stands still while nothing acts, so a resumed night
loses only the job in progress. We tested it by killing a night mid-run; the resumed night scored normally.

## How we developed it

1. **Discovery.** Probing the endpoint, reading the manual, building a client with pacing and logging.
2. **Rule agents v1 to v9** over Friday and Saturday, each fixing what the last night's logs showed.
3. **The porter rewrite.** An Opus model playing the busy hours of one night by hand scored 27% more than our agent
   over the same hours. Its habits (trips, a
   stocked cart, remembering guests) became `worker.py`, which beat v9 on every fresh seed.
4. **A tournament on paired fresh seeds.** Every change raced the current champion on the same new seeds; only
   measured winners stayed; each round used new seeds; seeds 900–999 were kept for one final exam. At 4 shifts at once
   we ran about 35–45 nights an hour, about 1,000 practice shifts in all.
5. **AI assistance throughout.** We built with Claude Code: subagents analysed the worst nights' logs and proposed
   patches (several of the winning flags came from this), model-driven agents played research nights to test
   hypotheses, and a seed skimmer recorded 221 nights' request streams.
6. **Hardening.** A crash sweep on simulated hotels with renamed places and reworded requests, a 149-night soak test,
   a tested final launcher, and dry runs on held-out seeds.

## What worked

- **Changing how the robot works, not tuning it.** The trip structure was worth +350 a night; most tuning was worth 0.
- **Paired comparisons on fresh seeds.** Nights vary by ±275 points, so only same-seed comparisons could see effects
  of 10–50 points. They also killed ideas that sounded right: extra stock, going down early, delaying searches,
  speaking without knocking, staying out of spare-stock detours, signing every patch on a floor were all about 0.
- **Reading the worst nights' logs.** The biggest fixes were bugs, not ideas: docking with unread mail, picking another
  request's luggage, a search loop that could kill a night, slips onto unseen floors.
- **Robustness.** The final ran with no errors, no restarts and almost no penalties; the traps were handled every time.
- **Honest estimates.** We predicted about 2,200 and scored 2,211.

## What didn't

- **We never closed the gap to the top.** We earned about 31 points a request; the winner about 35–36 on the same
  nights. Two free-exploring models, a command probe and every unrequested-work test found no hidden source of points.
- **Model-driven play lost to code.** Sonnet and Opus agents playing through our tools scored 0–130 below the rule
  agent, mostly on battery management and late-night trips, even with foreknowledge of a night's requests.
- **Some findings came too late.** The slip mechanism and value-per-minute ordering were found hours before the final.
- **Too few test nights.** The leading teams ran about 140 shifts an hour at times; we ran about 40. Small gains need
  dozens of paired nights each to confirm.
- **The judgment score.** Ours was below most teams' despite almost no penalties; we never worked out what it rewards.

## Where the remaining points were

On the final's own nights (per night, averaged over our ten):

| | Points |
| --- | --- |
| On offer if every request were done instantly | about 2,650 |
| **We scored** | **2,163** |
| Lost to waiting (jobs done, but after a delay) | 307 |
| Do Not Disturb doors | 80 |
| Spill reports with no patch to sign | 69 |
| Items out of stock | 41 |
| Penalties, requests open at 06:00 | 19 |

The winner's 2,487 is above what any robot could reach without recovering some of the Do Not Disturb, spill and
stock losses we treated as impossible, so they were both faster and found ways through at least one of those.
From our own logs, the unfinished levers were: ordering under load (+15–30 a night), going down sooner for waiting
trays and bags (+10–20), smarter lost-item searches (+10–20), fewer slips (+10–20), charging before the rush
instead of during it (+5 on average, +30–60 on busy nights), and starting to serve before the map is complete
(+10–15).

## Running it

Python 3, no dependencies beyond the standard library; the language-model reader needs the `claude` CLI. Create
`.env` with your own key (never commit it):

```text
NIGHTSHIFT_KEY=<team key>
NIGHTSHIFT_URL=https://nightshift.wybe.me/
```

```sh
NS_WFLAGS=wake,seek,dndsay,signnear,smith,lookahead python3 worker.py 50   # one practice night
NS_AGENT=worker.py NS_WFLAGS=wake,seek,dndsay python3 run_set.py lab 50 59 # a seed range, unattended
python3 final_run.py --practice 905 906                                     # the final launcher on practice seeds
NS_ALLOW_FINAL=1 python3 final_run.py --final 10                            # the real final
python3 ns.py '{"cmd":"whoami"}'                                            # one raw command
```

`logs/STOP` halts every request. The client refuses final-mode requests unless `NS_ALLOW_FINAL=1` is set.

## Files

| File | Purpose |
| --- | --- |
| `worker.py` | The final agent (flags in `NS_WFLAGS`; `NS_FAST=1` walks with `navigate`) |
| `final_run.py` | The final launcher: ten nights, retries, saved state, resume |
| `harness.py` | The robot's world model and primitive actions |
| `agent.py` | Request reading and job routines (and our earlier v9 agent) |
| `llm.py` | Claude reader for unfamiliar wording and item names |
| `ns.py`, `bot.py` | The client: pacing, retries, logging, safety blocks |
| `run_set.py` | Runs a seed range, one process per night, writes a summary |
| `sonnet_night.py`, `play.py` | Model-driven agents for research nights |
| `skim.py`, `probe_interface.py` | Records a night's requests cheaply; probes for hidden commands |
| `sim/` | Local simulator, built from our assumptions: good for crash tests, not for decisions |
| `chaos.py`, `explore.py`, `probe.py`, `crawl.py`, `watch.py`, `gates.py`, `experiment.py` | Discovery-phase tools |
| `FINAL.md` | Our launch checklist and the record of the final |
| `STATE.md` | Our working notes during the event |
