# eta

A skill that makes an AI coding agent give ETAs you can plan around. Type `/eta` and it answers "how long will this take?" or "how long is left?"

It covers plans, the agent's own work, and long-running jobs: training runs, builds, renders, pipelines, downloads and cloud jobs. It was written for Claude Code and works with any agent that can read a Markdown file and run a Python script.

## The problem

Agents are bad at time, in two opposite directions.

- **They overestimate their own work.** A study of Claude Code and Codex on 435 tasks found predictions about 3x to 10x too long, worst on short tasks. A second study found 4–7x. The models repeat the timelines humans wrote in their training data and tend to guess about 90 minutes whatever the task.
- **They underestimate machine jobs.** In the session logs this skill was built from, guesses for cloud GPU runs came in 1.3–1.7x too short five times out of five. The misses were queue time, failed first launches, slow downloads and steps nobody had timed.
- **They can't feel elapsed time.** Asked "how long is left?" three times during one run, an agent said "30–60 more minutes" each time. With a clock, the same kind of question is answered almost perfectly.

More thinking does not fix this. In the studies, giving the model more detail about the task did not improve its estimates. What forecasting research recommends instead is the outside view: compare against measured results from similar past work. That is what this skill does.

## How it works

`SKILL.md` gives the agent seven steps. `scripts/eta.py` holds the clock and the history.

1. Split the work into parts by kind: `own`, `compute`, `remote`, `queue`, `transfer`, `human`.
2. Get a raw range for each part. Machine parts are timed on a sample first. Own work is counted in steps, not human effort.
3. Record the estimate. The script corrects each part from history and prints the most likely time, a range and the finish times.
4. Make the job print its progress before launching it.
5. Mark each change of part, so every part is timed by the clock.
6. Answer "how long is left?" from the job's measured speed, never from the old estimate.
7. Close the estimate when the work finishes, so the log learns.

The user sees something like this:

```
ETA: about 2 h 55 min after the job leaves the queue (2 h 40–3 h)

  GPU run           about 2 h 40   measured: pilot, 49 s per language
  Download, checks  about 15 min   guess
  Kaggle queue      not counted    37 min and 2 h 25 min the last two times

Next update: when the job leaves the queue, with a finish time.
```

## Install

Claude Code, for all your projects:

```
git clone https://github.com/<you>/eta ~/.claude/skills/eta
```

For one project, clone into `.claude/skills/eta` inside it. The script needs Python 3.8 or later and nothing else.

Then type `/eta`, or `/eta the full training run`.

The skill runs only when you type it. That is set by `disable-model-invocation: true` at the top of `SKILL.md`, which also keeps the skill out of the agent's context until you call it. Delete that line if you want the agent to use the skill on its own whenever it states a duration.

For other agents, put the body of `SKILL.md` in the rules file and give the full path to `scripts/eta.py`.

## The script

```
eta.py plan "label" KIND=LO-HI[:m] ...       record an estimate; print it corrected, with finish times
eta.py step ID KIND [--at TIME]              the part KIND starts now, or started at TIME
eta.py check [ID] [--done N --total M]       elapsed and remaining; with progress, a rate projection
eta.py done ID [KIND=ACTUAL ...] [--at TIME] close it and record the real durations
eta.py drop ID                               cancel it
eta.py stats [KIND]                          past estimates against real durations
eta.py add KIND ACTUAL [LO-HI[:m]]           back-fill a known result
```

Durations are written `45s`, `25m`, `1.5h`, `2h30m` or `2d`. A bare number is minutes. `:m` marks an estimate as measured. A bare kind with no range takes its range from past real durations.

An example, with a queue wait that can't be predicted and a run that was timed on a pilot:

```
$ eta.py plan "measured run" queue/kaggle remote/kaggle=2h30m-2h50m:m transfer=5m-10m own=10m-20m
#2 measured run | now 20:46 IST
queue/kaggle   (history)   -> about 1h30m (37m-2h25m)    from 2 past queue/kaggle durations
remote/kaggle  2h30m-2h50m:m -> about 2h40m (2h30m-2h50m)  measured, no history
transfer       5m-10m      -> about 7m (5m-10m)          UNMEASURED GUESS, no history
own            10m-20m     -> about 4m (2m-7m)           prior x0.15-0.5: agents overestimate own work; no history yet
TOTAL about 4h20m (3h25m-5h15m) | finish around Mon 01:07 (Mon 00:12-02:02) IST
SPREAD comes mostly from queue/kaggle. Without it: about 2h50m (2h40m-3h). Quote that part apart.

$ eta.py check 2 --done 3 --total 18
remote/kaggle: 3/18 -> about 5h (4h20m-6h15m) more for this part (rate since this part started; a second reading later gives a tighter figure)

$ eta.py check 2 --done 6 --total 18
remote/kaggle: 6/18 -> about 2h (1h50m-2h20m) more for this part (rate since the reading at 22:40)
```

### How estimates are made tight

- **A most likely time leads.** Each part gets one most likely value, and the range sits in brackets after it.
- **History sets the range.** Each finished part stores its raw estimate and its real duration. Once a kind has 3 results, a new estimate is its midpoint times the median past ratio, and the range comes from the lowest and highest past ratios (the 10th and 90th percentiles from 8 results on). The range the agent wrote is dropped, because past scatter is the better guide.
- **Parts are not stacked.** The total adds the most likely values and combines the spreads in quadrature. Adding every low and every high assumes all parts go wrong together, which is rare.
- **The unpredictable part is quoted apart.** When one part causes most of the spread, usually a queue, the script prints the estimate without it.
- **Time remaining uses recent speed.** A second progress reading gives the speed between the two readings, which leaves out startup time.
- **Guesses and measured estimates keep separate histories.** A tag such as `remote/kaggle` falls back to all of `remote` until it has 3 results of its own.
- **Own work starts with a prior.** Unmeasured guesses of the agent's own work are multiplied by 0.15–0.5, taken from the studies below. History replaces the prior after 3 results.
- **No number without a basis.** Other guesses with no history are flagged `UNMEASURED GUESS`. When a part runs past its range and no progress is given, the script refuses to print a time remaining.

### What the range means

The range is a likely range, not a guarantee. With 5 past results, a new result lands inside about 2 times in 3, and more often as the log grows.

A check on five real cloud GPU guesses, each one predicted from the other four:

| | Agent's raw guess | Corrected |
|---|---|---|
| Real time inside the range | 0 of 5 | 3 of 5 |
| Most likely value, average miss | 34% | 8% |
| Range width, as a share of the real time | – | 25% |

A wider rule (stretching the agent's own range by the history) caught 5 of 5, but its ranges were 67% of the real time. Five cases is a small sample.

### The log

The log is a JSON Lines file at `~/.local/share/agent-eta/log.jsonl`. Set `ETA_LOG` to move it. It stays on your machine and holds only labels, kinds, estimates and times. It is shared across projects, so history from one project improves estimates in the next.

## Limits

- Correcting from history is the standard fix in forecasting, but nobody has published a test of it on coding agents. The studies below measured the problem, not this cure.
- A tight range needs a measurement. A part that was only guessed, with no history, stays wide and is flagged.
- Early on there is little history, and a range from 3 results is rough. It improves as the log grows.
- The agent has to follow the steps. If it skips `step` and `done`, the log learns nothing.
- Jobs that hide their logs until the end (some notebook platforms) can't be projected mid-run. The skill tells the agent to say so and give the platform's time limit.

## Tests

```
python3 -m unittest discover -s tests
```

## Sources

- Ofengenden and Andriushchenko, [Your Agents Are Not Time Aware](https://www.lesswrong.com/posts/eAbuPXbjakop5rSJx/your-agents-are-not-time-aware) (2026). Claude Code and Codex over-predict task time by about 3x to 10x; a clock tool fixes judgments of elapsed time.
- Garikaparthi, [Can LLMs Perceive Time? An Empirical Investigation](https://arxiv.org/abs/2604.00010) (2026). Pre-task estimates overshoot by 4–7x.
- Monperrus, [Coding agents have a completely wrong sense of time](https://www.monperrus.net/martin/coding-agents-sense-of-time). Practical fixes: timestamps, logged durations, no human time units.
- [Agent Time Estimates Are Not Schedules](https://agentpatterns.ai/human/agent-time-estimates-not-schedules/). Treat an agent's estimate as a scope signal and measure past runs.
- Kahneman and Tversky on the planning fallacy, and Flyvbjerg on reference class forecasting, for the outside-view method.

## License

MIT
