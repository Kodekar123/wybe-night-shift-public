# Final launch checklist (Sunday 8 Nov, 12:00–13:00)

The final runs 10 secret nights; the score is the mean after dropping the worst night. All teams get the same ten
nights. Results are revealed at 13:05. Only `final_run.py` starts final shifts, and only with `NS_ALLOW_FINAL=1` and
`--final`. Only first place pays, so the final plays the higher-upside combination (see "Final agent").

## Final agent

`worker.py` with flags `wake,seek,dndsay,signnear,smith,lookahead` (the default of `FINAL_WFLAGS` in `final_run.py`),
normal mode (no `NS_FAST`), language-model reader on (`NS_LLM=1`, needs the `claude` CLI).

| Flag | What it does | Evidence |
| --- | --- | --- |
| `wake` | Never dock with a message unread; claims always declined (+5) | +53/night, won 8 of 8 |
| `seek` | Search the whole ground floor for lost items, empty spots last | +20/night, won 8, lost 0 |
| `dndsay` | Speak messages and noise warnings through Do Not Disturb doors | +24/night, won 8 of 12 |
| `signnear` | Sign a spill from the next corridor once the patch is seen | +11/night, won 9 of 12; slips 29 -> 21 |
| `smith` | Order ready jobs by value lost per minute / minutes to do (Smith's rule) | +6, +37 on the 2 busiest seeds |
| `lookahead` | Look before entering a corridor not seen for 15 min; no detours round known wet floors | +34/night mean, median +2.5 |

The combination: +13.5/night over the dry-run seeds 905-908 (won 3, lost 1), no errors, no penalties.

Fallbacks (set `FINAL_WFLAGS=...` before launching): `wake,seek,dndsay,signnear` (dry run 2,257 mean) or
`wake,seek,dndsay` (validated on 174 nights, mean 2,150).

## The launch (scheduled)

`logs/final-launch.sh` is running and fires at **12:00:00** by itself (approved by the user). It logs to
`logs/final-run.out`, checks that no practice shift is live, removes `logs/STOP`, then runs:

```
FINAL_DEADLINE=<12:40 epoch> NS_ALLOW_FINAL=1 python3 final_run.py --final 10
```

To launch by hand instead (if the scheduled script is gone):

```
cd /Users/armand/Developer/oslosprint/wybe
FINAL_DEADLINE=$(python3 -c "import datetime;print(datetime.datetime(2026,11,8,12,40).timestamp())") \
NS_ALLOW_FINAL=1 nohup python3 final_run.py --final 10 > logs/final-run.out 2>&1 &
tail -f logs/final-run.out
```

## What happens during the final

1. 10 night processes start 2 seconds apart, each asking for a final shift (`start_shift`, `mode: final`, no seed).
2. A refused start (not open yet, a cap on concurrent shifts) waits 30 s, then up to 2 min, and retries; with a cap of
   4 the nights simply run in waves. Nothing new starts after 12:40.
3. Each night plays to 06:00 hotel time on its own: about 12-15 min of wall time with 10 at once (shared pacing of ~18
   requests a second), or ~3 waves of ~6 min with a cap of 4.
4. The worker saves its full state every step. If a process dies while its shift is live, the launcher restarts it
   on the same shift from that state (up to 3 times); hotel time stands still meanwhile.
5. At the end the launcher prints every score, the mean, and the mean without the worst.

Logs: `logs/final-run.out`; per night in `logs/final/<MMDD-HHMM>-final/` (launcher.txt, a log, run json, state file).

## What to watch, and what to do

- **First minute:** the starts. If every start is refused with something other than "not open yet" or a shift cap
  (e.g. a missing argument), fix the launcher and relaunch; adjusting the launcher is not controlling the robot.
- **A night stuck** (no progress in its log for minutes, shift still live): kill its `worker.py` process; the
  launcher resumes it from the saved state.
- **The launcher died:** `python3 ns.py '{"cmd":"shifts"}'` lists live shifts; resume one with
  `NS_RESUME=<shift> NS_MODE=final NS_ALLOW_FINAL=1 NS_STATE_FILE=<its state-*.pkl> NS_WFLAGS=wake,seek,dndsay,signnear,smith,lookahead python3 worker.py 0`.
- **Power:** the Mac must stay on mains; `awake status --json` shows the hold (until 14:37).
- Never control the robot by hand in the final (rule).

## Before the launch (done Sun 11:20)

- [x] Practice testing finished; no live shifts; no `logs/STOP`.
- [x] Mac on mains, `awake` hold until 14:37, watcher running.
- [x] Autopilot paused until 13:30 so nothing else starts.
- [x] `claude` CLI present (2.1.319); board logger running.
- [x] Launcher default flags verified: `wake,seek,dndsay,signnear,smith,lookahead`.

## Tested

- Sat 23:41 and 23:47: practice dry runs through the launcher; worker killed mid-night both times. Without saved state
  the resumed night scored 1,084 (map lost); with saved state (now default) it scored 2,036, a normal night.
- Sun 01:30: robustness fix. On reworded/renamed simulated hotels the champion crashed 2 of 6 nights (a lost-item
  hint mapped to a fixed place name, the walk failed every step, the night died at the error cap). Fixed: hints map
  only to places that exist (the kitchen found by meaning), searches skip unreachable places, and any job failing 3
  times is reported failed and dropped. Afterwards 30 of 30 simulated nights (20 reworded, 10 normal) ran clean.
- Sun 03:47: held-out exam, seeds 901-904, launcher in final configuration (`wake,seek,dndsay`): 2,671, 2,503,
  1,745, 1,677; mean 2,149, mean without worst 2,306. Matches validation (2,123 over 24 fresh seeds). No errors.
- Sun 04:50-09:08: soak test, 149 fresh nights of `wake,seek,dndsay`: all ended normally, 0 step errors, 0 give-ups;
  spill penalties on 2% of spill jobs (-2.2/night). Busy nights can run the battery low (12% of nights).
- Sun 09:10: slip finding. All 358 soak slips (-15 and a minute each, ~2.4/night) were onto wet floors the robot had
  not seen; 0 slips in 4,432 crossings of floors it had seen wet. Unframed Opus explorers (2 nights) found no hidden
  point source: pay is speed from arrival, credited at hand-over, +5 at report.
- Sun 10:04: signnear adopted (+11/night over 12 paired seeds); lookahead then not adopted (median +2.5).
- Sun 10:11: dry run seeds 905-908 with `wake,seek,dndsay,signnear`: 2,492, 2,234, 2,362, 1,940; mean 2,257.
- Sun 11:07: busiest seeds 646 (93 requests) and 542 (91): smith +37 and +6 against the frozen agent.
- Sun 11:16: dry run seeds 905-908 with the full combination: 2,552, 2,259, 2,316, 1,955; mean 2,270 (+13.5), no
  errors, no penalties. Switched the final to it (only first place pays).

## Result (Sun 12:17)

Final nights (wake,seek,dndsay,signnear,smith,lookahead): 1,726, 1,950, 2,028, 2,244, 2,573, 1,892, 2,465, 2,044,
2,402, 2,305. Mean 2,163; **mean without the worst night 2,211** (estimate before the final: ~2,200). All 10 started
at 12:00:05 with no refusals, ran in parallel, ended at 06:00 hotel time by 12:17; no errors, no penalties, no
resumes needed. Server confirms the 10 final shifts and scores; none left open.

## Standings (revealed 13:05)

7th of the field with 2,211. 1 Spirhed 2,487; 2 Bando 2,410; 3 Good company 2,377; 4 Three Guys One Repo 2,298;
5 AIKI AS 2,287 (best judgment, 5,670); 6 Forsterk 2,272; 7 Cobol 2,211 (judgment 4,775, 16,201 API calls);
8 Pied Piper 2,209; 9 Fettero 2,175; 10 Ctrl Alt Vibe 2,159.
