---
name: eta
description: Estimate how long work will take, or how much time is left on a running job. Runs only when the user types /eta.
disable-model-invocation: true
argument-hint: "[what to estimate]"
---

# ETA

You have no clock and no feel for your own speed. Unaided, agents overestimate their own work several times over (they repeat human timelines) and underestimate machine jobs (they leave out queues, startup, retries and downloads). So never state a duration from feel. Every number comes from a measurement or from the log, or is labelled a guess.

`E` below means `python3 scripts/eta.py`, with the path taken from this skill's directory.

## What to estimate

- Text after `/eta` names the work.
- With no text, run `E check`. If an estimate for the current work is open, report time remaining (step 6). Otherwise estimate the work now planned or running in this conversation.
- `E check` also lists estimates left open by earlier sessions. Close the ones that finished (`E done ID --at TIME`) and drop the ones that were cancelled (`E drop ID`).
- Steps 5 to 7 belong to the estimate you gave, so do them later in the session without being asked. Don't start a new estimate until the user types `/eta` again.

## Steps

1. **Split the critical path into parts by kind.** The kinds err in opposite directions, so they are estimated apart.

   | Kind | What it covers |
   |---|---|
   | `own` | Your reading, thinking, editing and tool calls |
   | `compute` | A job on this machine: training, render, build, tests |
   | `remote` | A job's run time on another machine: cloud GPU, CI |
   | `queue` | Waiting for a scheduler, runner or quota |
   | `transfer` | Downloads and uploads |
   | `human` | Waiting on the user: an approval or a manual step |

   Add a tag to keep histories apart: `remote/kaggle`, `compute/render`. Leave out work that runs in parallel off the critical path.

2. **Get a raw range for each part.** Measuring is what makes an ETA tight: a timed part is usually within 10%, a guess is not.
   - **Machine parts: time a sample first.** Run one unit or a small pilot, then take rate × units, plus fixed costs (install, loading data and models, first-step compile). Mark it measured with `:m`. A rate from other settings (model, resolution, hardware, network) is a guess, not a measurement.
   - **`own`: count concrete steps** (files, edits, runs, check-and-fix rounds), never human effort. No days, weeks or phases. Give your honest raw range and don't shrink it; the script applies the correction.
   - **`queue`, or anything you can't measure:** pass the bare kind with no range to use past durations from the log. With no history, give a range and call it unmeasured.
   - **First launch of new code:** add time for one failed launch and a relaunch.

3. **Record it.** `E plan "label" queue/kaggle remote/kaggle=2h30m-2h50m:m transfer=5m-10m own=10m-20m`
   It prints each part corrected by history, then the most likely total, its range and the finish clock times. Quote those numbers as printed. Don't use your raw ones, and don't widen or narrow the range.

4. **Make progress readable before you launch.** Have the job print `done/total` with a timestamp, flushed, somewhere you can read mid-run. If the platform hides logs until the end, say so in the ETA and give its hard time limit.

5. **At each change of part** (queue ends, job ends, download starts): `E step ID kind`. Add `--at HH:MM` if the change happened before you noticed it.

6. **For time remaining:** `E check ID --done N --total M`, using the job's real progress in equal units (steps or epochs, not runs of different length). A second reading later is tighter, because the script then uses the speed between the two. Never answer by subtracting elapsed time from the old estimate. If the part is past its range and you have no progress signal, say exactly that, give no new number, and check for a hang.

7. **When finished:** `E done ID`. If it landed outside the range, tell the user in one line why. This step is what makes the next estimate better.

Other commands: `E stats [kind]`, `E add kind actual [estimate]` (back-fills a known result).

## Reply format

Lead with the most likely time. Put the range in brackets after it.

```
ETA: about 2 h 55 min, done around 18:40 IST (18:30–18:55)

  GPU run           about 2 h 40   measured: pilot, 49 s per language
  Download, checks  about 15 min   guess

Late if: the download is slow.
Next update: when the run reaches half way.
```

When the script prints a `SPREAD` line, one part is unpredictable. Quote the estimate without it and state that part on its own:

```
ETA: about 2 h 55 min after the job leaves the queue (2 h 40–3 h)

  GPU run           about 2 h 40   measured: pilot, 49 s per language
  Download, checks  about 15 min   guess
  Kaggle queue      not counted    37 min and 2 h 25 min the last two times

Next update: when the job leaves the queue, with a finish time.
```

- Each line says where its number came from: measured, history or guess.
- Under about 5 minutes in total: one line ("ETA: about 4 min") and no log entry.
- `human` parts go on their own line and are not counted in the finish time.
- Projects that span sessions: give work time and calendar time separately. Calendar time is set by quotas, session caps, queues and waits on the user.
