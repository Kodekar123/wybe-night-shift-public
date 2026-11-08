# Fresh-start brief

Written Sat 7 Nov ~14:15 for a new Claude context. **You are not bound by anything the previous context planned or
built.** Below are only verified facts, hard rules, results as data, and what is still running. Form your own view of
how to win. Practice closes Sun 8 Nov 11:45; the final is 12:00–13:00: 10 secret nights at once, scored as the mean of
ten minus the worst. Organisers' only hint: "Do tasks correctly, quickly and safely."

## Goal and working style (from the user)

- The highest mean on unseen nights. Strategies must generalise; never tune to one hotel.
- Explore freely and test on the real server. Use subagents heavily: Sonnet for exploration and mechanical work, Opus
  where judgement matters. Don't get bogged down in existing code or assumptions; rebuild whatever isn't certain.
- Think like a skilled worker: know where to go, act, don't over-plan details.
- Push back on the user when you disagree. Notify them (PushNotification) only for a major breakthrough.
- An autopilot Stop hook (`scripts/keep_working.py`) keeps you working until our best fresh-seed mean-minus-worst
  reaches 85% of the board's best night, or Sun 11:30, or `logs/AUTOPILOT_OFF` exists. It lets you stop while a
  `run_set.py` is running, so start a harness-tracked background waiter that wakes you when work ends.
- `logs/AUTOPILOT_PAUSE_UNTIL` (epoch seconds) pauses the autopilot until that time; one is set to Sat 16:00 for
  the context reset. Delete it to resume at once.
- Keep the Mac awake for long work: `awake on <dur> --json`, then `awake wait --json` in the background (needs AC).

## Hard rules

- Never send `mode: final` before the deliberate launch.
- Seeds 900–999 are unseen; keep them for one final evaluation.
- Server limits, measured: at most **4 live practice shifts per team**, about **20 requests a second per team** (429 "Too
  many requests. Slow down." above). Stay under both.
- End only shifts you started (match ids in your own logs); other tools share the key.
- Never print or commit `.env` (the key). Commit and push only with the user's agreement.
- Don't overload, attack or probe the server beyond game commands.

## Verified facts (each measured on many nights unless marked)

- **Protocol:** `POST https://nightshift.wybe.me/`, `Authorization: Bearer <key>`, body `{"cmd", "args"}`, shift id
  from `start_shift` sent as `X-Shift`. Hotel time 22:00–06:00 moves only when the robot acts.
- **Commands and costs:**

  | Command | Hotel time | Battery |
  | --- | --- | --- |
  | `move` (adjacent) | ~1 s/m | 0.02%/m |
  | `navigate` (any visited place) | ~1.25 s/m, takes stairs, not elevators | |
  | stairs | ~3 s/m, no cart | |
  | `call_elevator` (at the panel) | 45 s, plus a 15 s ride | 0.1% |
  | `look` | 5 s | 0.1% |
  | `pick` / `place` | 10 s | 0.2% |
  | `scan` | 60 s | 1% |
  | `knock` / `say` / `read` / `ask` | 15 / 5 / 20 / 30 s | 0 |
  | `status`, `inbox`, `report`, `reply` | 0 | 0 |
  | `dock` | +1%/min, wakes on a new message, refuses at full battery | |

  Hidden commands are listed in the staff-room manual. Two hands fill before the cart; you can't place into the cart.
  The cart holds 8, and trays and luggage need it.
- **Pay:** points come only from a correct `report ... done`. Fitted on about 4,000 jobs, it predicts held-out seeds to
  within 2.8 points: `base(kind) × (1 + 0.075 × hours since 22:00) × (0.21 + 0.79 × e^(−minutes late / ~22))`. Fresh
  bases: spill 54, lost 51, bags 44, tray 40, check 38, noise 30, delivery 28, message 23. Declining a false "never
  came" claim is +5; `failed` is 0. Pay depends on the clock, not on earlier work (hold test). No end-of-night bonus.
- **Penalties:** dead battery −200 (ends the shift); letting the man in a bathrobe into a room −80 (−54 to −142 seen,
  and he slips in when a keycard opens a door near him, including a room he asked about minutes earlier); another
  guest's lost item −60 to −90; room check reported without looking inside −25; false `done` −20 to −40; slip on an
  unseen wet patch −15 and 60 s (a `look` shows wet floors here and next door); wrong item or room −10.
- **Traps:** `note` objects are prompt injections; forwarded calls ("bring it to my friend") aren't orders; "Skip
  R-0xx" cancels; "never came" claims are false or duplicates; Do Not Disturb signs appear overnight and stay. Lost
  items are usually labelled in request order (`toy`, `toy-2`, ...), but the seed 705 explorer found a case where
  the label didn't match; confirm the owner (hint place, clerk) before returning one. Vague places are resolved by the clerk or by what `look`
  shows (noise often comes from a neighbouring room).
- **Nights:** the request stream is fixed by the seed (same 82 requests whether the robot worked or idled); 50–88
  requests a night; about 60% arrive while busy. Stock is finite, never restocked.

## Results, as data (not conclusions)

| What | Nights | Result |
| --- | --- | --- |
| v9 rule agent against v6 on the same seeds 50–59 | 10 | Mean 1,954 against 1,862; won 8 of 10 |
| v9 on 700, 701 | 2 | 2,126, 1,711 |
| Clean-room Opus that wrote its own program from scratch | 5 | Mean 1,461 |
| Opus playing by hand, informed, 22:00–00:30, seed 711 | 1 partial | 2.9 min arrival to report, 31.8 points per request; the rule agent over the same hours averages 7.0 min, 26.8 |
| **Opus playing by hand, informed, seed 705, 02:02–06:00, same start as our agent** | 1 (busy hours) | **+1,614 against v8's +1,275 over the same hours (+27%); 2,400 against 2,071 for the night.** 37.5 per done request, 4.8 min arrival to report. Took 49 min of wall time for 4 hotel hours. Its notes and copyable rules: `../o705/notes.md` and the bottom of this file |
| Opus playing by hand, uninformed, seed 705 | 1 partial | 1,072 by 02:53, about the same as v8 by then; 32 min of wall time for 5 hours |
| Speaking a message before knocking | 13 paired | No clear effect |
| Stocking the cart at the start of the night | 13 paired | +29 on 8 nights, mixed before; within noise |
| Waiting at the ground-floor elevator (with scans) | 5 | Hurt: battery drain |
| Exact order search over open jobs | 8 paired (740–747) | Mean 1,740 against 1,760; within noise |
| Model as dispatcher (code does the work), simulator only | 15 | Within ±3% of the rules |
| Job ranking rules; battery thresholds | simulator, hundreds | No effect |
| Unrequested work, delegation, argument tricks | explorers | Nothing paid |
| Board: top team runs ~140 shifts an hour | board log | Implies ≤ ~500 calls per night (our agent uses ~1,600–2,000) |

Night-to-night swings are ±150 points, so a change needs several paired nights to show.

## Session 2 (from Sat 15:00): what this context found and is doing

- Board at 15:00: leader's best night 3,448 (1,479 shifts), Bando 3,320 on only 170 shifts; ours 2,400.
- A night takes ~2 min of wall time; the 4-shift cap and ~18 req/s allow ~30 real nights an hour.
- Diagnosis over 25 v9 nights against a 2-minute ideal: speed losses ~410/night and failure losses ~420/night.
  Execution (not queueing) dominates: tray 5.5, bags 5.4, delivery 3.9 min median per job. The elevator call is a fixed 45 s.
- Failures per night: spill "found no spill" ~1 and "no sign" ~0.5 (~60 pts each), lost item not found ~1.3, DND ~2.8.
- Bug found: the clerk sometimes answers about someone else ("Which room is Mx Solberg in?" -> "Ms Berg is in
  room-102."); v9 trusted it and lost -30 twice on seed 700 although it had knocked at Solberg's door 5 times.
- Code flags (env `NS_FIX`, comma list; default `names,keycard`; `NS_FIX=""` is v9):
  `names` learn guests from knocks, check the clerk's answer names the guest, refuse hand-over to the wrong guest
  and retry; `keycard` pick the key at the desk on bags jobs; `batch` pick trays/bags for other waiting jobs at
  the kitchen/desk (cart).
- Test t1: base vs names+keycard, seeds 760-771, labels t1-base-a/b, t1-fix-a/b.

### Session 2 results (Sat 16:20)

- **worker.py** (new, trip-based: triage, one ground-floor pass, floor by floor, stocked cart, names from knocks):
  12 fresh seeds 760-771, beat v9 on all 12, +351/night, mean 2,207, mean minus worst 2,244 (was 1,982). Current champion
  and fallback for the final. v9 with fixes (`NS_FIX=names,keycard`): +48/night over 8 (t1-*).
- **Sonnet agent** (`sonnet_night.py` + `play.py`: the model picks what/when, code does `do R-id` / `ground` with proof):
  1 real night, 2,172 on 760 (worker 2,312). Prompt plays to win with the pay table. Logs per command in
  logs/play/<label><seed>-commands.jsonl, transcripts in -session<n>.jsonl, run json in the usual format.
- Bugs fixed: idle loop at full battery burned ~8 calls/35 s (a night hit 3,000 calls); ns.py budget now 4,000 per
  process and stops only that process. run_set gives each run its own result file (NS_RUN_FILE), NS_AGENT picks the agent.
- Generality: on reworded/renamed sim hotels with the meaning matcher on, worker 2,112 vs v9 1,944. User: don't lean
  on the simulator; real nights decide.
- Tournament (user's idea): champion vs challengers on the same fresh seeds each round, new seeds every round,
  held-out 900-999 once at the end. Round 1 (seeds 772-783): r1-champ, r1-return, r1-stock2 (worker flags in
  NS_WFLAGS), r1-sonnetA (competitor), r1-sonnetB (explorer, writes logs/play/insights.md). User wants >=2 Sonnet lanes.
- Gotcha: a background waiter whose command line contains the pgrep pattern matches itself; wait on result files.

### Session 2, Sat 18:00

- Ceiling analysis (24 worker nights): instant-everything averages 3,145/night; we get 2,097 (67%). Slowness costs
  ~711/night, failures ~255. Leaders' 3,448 fits ~85% of a busy night: the gap is mainly speed.
- `wake` (never dock with an unread message; claims always declined): +58/night over 6 seeds, won 6/6. New champion.
- Research lane (Opus, PLAY_ALLOW_DONE=1, logs/play/research_guide.md): speaking messages/noise without knocking pays
  in full (7/7); pay is credited at the action, report adds +5; DND rooms are locked (unavoidable); out of stock is
  real; a sign can be placed from the next corridor. Findings in logs/play/insights.md.
- Sonnet B (free challenger brief, logs/play/explorer_guide.md): -23/night over 9 r1 seeds, won 2. Sonnet A retired.
- Challengers queued/flags: noknock (speak without knocking, look kept for DND), stocklate (no spare-stock detours
  while busy), lostlast (no signal). NS_FAST=1 = navigate for same-floor stretches (for round 3, both arms).
- Throughput: ~32 nights/h; per call ~176 ms with keep-alive (was 219); rate cap 18/s binds with 4 lanes, so calls
  per night is the lever (now ~1,550; leaders imply ~500). Leaders ran ~140 shifts/h overnight, jumped 2,955->3,448
  at Sat 07:00, then mostly stopped.
- Chains: logs/r2-chain.sh (wake,stocklate after r2-wake), logs/r2-chain3.sh (wake,noknock after r2-champ).

### Session 2, Sat 23:30

- Champion now `NS_WFLAGS=wake,seek,dndsay` (r5: +8/night over 12 vs wake alone; seek +20, dndsay +24 separately).
  Fast mode (NS_FAST=1) costs ~28/night; the final should run normal mode. Validation run: val-champ seeds 844-855.
- Ruled out tonight (measured): hidden commands (probe of ~80 verbs, readables, arg variants: nothing new); pay clock
  starts at arrival, not at reading; no fast-completion bonus (pay/fresh: 0.85 at 0-1 min, 0.77 at 4-5, smooth);
  report/door wording doesn't change pay; request stream fixed per seed; trays/bags/lost items appear only when
  requested (foreknowledge can't prefetch); delivering on "never came" claims pays 0 (decline +5); DND signs are
  time-driven (0% before 00:00 to ~6% after 04:00), not caused by our visits; floor of the next request is random.
- Requests per night: mean ~68-70, sd ~9, range 50-91 (151 + 41 skimmed nights). No very rich seeds.
- Board reading: best-of-N inflation explains part of the leaders' 3,000+, but Bando crossed 3,000 after 45 shifts;
  estimated typical nights of top teams ~2,450-2,700 vs ours ~2,160 (fast) / ~2,200 (normal). A 20-30% gap remains
  unexplained: hunt lanes (Opus, logs/play/hunt_guide.md, PLAY_LABEL=hunt disables auto-triage) are testing
  judgement/unrequested-work point sources. Foreknowledge night (Opus) scored 2,322 < worker 2,599 on seed 761.
- Tools: skim.py (seed skimmer, ~170-220 calls/night, NS_BUDGET must be large), probe_interface.py, watchdog in
  scratchpad (Monitor). Bug I caused: ending shifts by seed pattern also hit a lane's shift; match by shift id only.

### Session 2, Sun 03:45

- FINAL AGENT: worker.py, NS_WFLAGS=wake,seek,dndsay, normal mode. 24 fresh seeds (val-champ, val-champ2): mean 2,123,
  sd 337; simulated final (10 nights minus worst) median 2,170, 90% 1,984-2,354. Launcher final_run.py + FINAL.md
  (tested: refused starts retried, killed night resumed from saved state, normal score after resume).
- Robustness fix 01:30 (hints only to existing places, unreachable search spots skipped, 3 strikes per job);
  30/30 simulated reworded/normal nights clean.
- Tested and ~0 (dropped): signfloor +3/12, blockedspill -2/8, handin 0/6, signall (0 per placement), noknock 0,
  stocklate +2, stock2 -3, lostlast ~0, return (never fired).
- Judgement points: only +10 for clarifying "next to the elevator" with the clerk (already collected). Turning the
  stranger away, notes, claims (deliver=0, decline=+5), unreported spills, found items to the desk: all 0.
- Hunt lanes (36 Opus nights): no new point source. Model lanes play ~0 to -80 vs the worker.
- Held-out 901-904 via final_run --practice started 03:29 (901: 2,671).

### Session 2, Sun 11:20: FINAL READY

- Final agent: worker.py, NS_WFLAGS=wake,seek,dndsay,signnear,smith,lookahead (final_run.py default), normal mode.
  Switched from the 174-night-validated wake,seek,dndsay because only first place pays (user). FINAL.md has the
  launch, the evidence per flag, the fallbacks and what to do if something goes wrong.
- logs/final-launch.sh fires final_run.py --final 10 at 12:00:00 (user approved). Autopilot paused until 13:30.
- Late findings: slips happen only onto unseen wet floors; smith (value-per-minute ordering) helps on busy nights;
  battery runs low on ~12% of busy nights; no hidden point sources (2 unframed explorers).

## Tools available (use, change or ignore)

- `wybe/` (git branch `v9`, = GitHub `main`): the v9 agent. This is the fallback for the final if nothing beats it.
- `../wybe-lean/`: v9 with fewer calls (~1,600 a night).
- `../wybe-fast/`: dispatcher mode (`NS_DISPATCH=1`).
- `../wybe-exp/`, `../wybe-plan/`: older lab copies.
- `ns.py`: the client (logging, pacing under a lock, `STOP` file, final-mode block). `run_set.py <label> <first> <last>`
  runs a seed range; `experiment.py handover <seed> <HH:MM:SS>` plays to an hour and leaves the shift open; `sim/` is a
  local simulator (good for catching bugs, wrong on some rules, so don't trust it for decisions).
- Logs: `logs/requests.jsonl` (every real request), `logs/<label>/out-<seed>.txt` (per night, `END` line has the score).

## Reference

TextQuests (CAIS, arXiv 2507.23701): LLM agents in 25 long text adventures. Over long horizons models lose track of
state (hallucinated items, loops, can't retrace routes); hint booklets roughly doubled progress (Claude Opus 4.1: 33.9%
without, 68.0% with); progress flattens after about 500 steps; harm should be scored per step. Relevant if a model
plays a role in the agent: let code hold the state and give the model compact verified facts. Their code
(github.com/centerforaisafety/textquests) is a deliberately bare harness: system prompt, full untrimmed history, JSON
`reasoning` + `action`, the score shown every turn, prompt caching, 25 games in parallel. Possibly useful: show the
dispatcher the score change after each job.

Similar agent games and benchmarks (no public match for this game itself; it looks bespoke to Wybe):
- In every study found, a fast scripted executor decides each step and the language model works beside it. A model
  driving the step loop never won (Real-Time Reasoning Gym/AgileThinker arXiv 2511.04898; DPT-Agent 2502.11882;
  HLA 2312.15224; RRARA 2506.07223).
- Closest to our setup: LLM-guided heuristic design from simulation traces (arXiv 2608.09343). Dynamic orders, AGVs
  with batteries. A model rewrote the rule-based dispatcher between batches: it read the worst run's event trace,
  proposed several patches in parallel, and kept only those that won over repeated evaluation. 77.5 against about 62
  for the best fixed baselines; won on all 100 matched seeds. Main finds: charge before needed, prefer nearby tasks.
- Robotouille (2502.05227): LLM agents drop from 47% to 11% success when tasks overlap. Vending-Bench (2502.15840) and
  τ-bench (2406.12045): long runs derail, models forget orders, results are inconsistent across trials, so keep the
  order list and trap handling in code.

## Running at ~14:15 (check, and redo anything missing)

| What | Result lands in | When done |
| --- | --- | --- |
| Opus explorer playing seed 705 from 02:01 to 06:00 by hand | its report; notes in `../o705/notes.md` | Compare with v8's full night on 705 (score 2,071), request by request |
| Cancelled: Sonnet explorers on 745–747 busy hours (triage, lean on calls, energy) | not started | Optional; folders `../s-triage`, `../s-lean`, `../s-energy` have capped clients |

## Seed 705 explorer: what it did (its own account, one night)

- Kept a stocked cart (1 each of toiletry kit, pillow, charger, towel, plus 2 water bottles) and restocked when passing
  storage or laundry. Most deliveries then took 1–4 minutes.
- With 4 or more jobs open: all floor-1 pickups in one pass (desk luggage, kitchen trays, clerk lookups), then each
  floor in elevator order, finishing a floor before leaving it, never returning to floor 1 in between.
- Remembered names and rooms from knocks, saving trips to the clerk. Declined "never came" claims at once (+5).
- Looked before stepping into corridors on spill floors; still slipped 3 times (stepping out of an elevator onto a new
  spill; a spill reported mid-walk). With no signs in stock it took signs from already-paid patches, with no visible
  penalty. Two spill reports mean two different patches.
- Losses: about −140 (3 slips, a spill reported as a duplicate −30, a wallet returned to the wrong guest about −64).
- "Noted. Thanks." means paid; "Noted." means rejected or zero.
