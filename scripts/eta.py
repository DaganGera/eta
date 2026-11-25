#!/usr/bin/env python3
"""eta.py - record estimates, time the real work, correct new estimates from history.

  plan "label" KIND=LO-HI[:m] ...   record an estimate; print it corrected, with finish times
  step ID KIND [--at TIME]          the part KIND starts now (or started at TIME)
  check [ID] [--done N --total M]   elapsed and remaining; with progress, a rate projection
  done ID [KIND=ACTUAL ...] [--at TIME]   close it and record the real durations
  drop ID                           cancel it (work was stopped)
  stats [KIND]                      past estimates against real durations
  add KIND ACTUAL [LO-HI[:m]]       back-fill a known result

KIND is own, compute, remote, queue, transfer or human, with an optional /tag.
Durations: 45s, 25m, 1.5h, 2h30m, 2d; a bare number is minutes. ":m" marks a
measured estimate. A bare KIND (no "=") takes its range from past real durations.
TIME is HH:MM or an ISO timestamp, for when you learn of a change after it happened.
Log: $ETA_LOG, or ~/.local/share/agent-eta/log.jsonl. Standard library only.
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

KINDS = ("own", "compute", "remote", "queue", "transfer", "human")
MIN_N = 3  # past results needed before history corrects an estimate
# Used only for unmeasured own-work guesses until MIN_N results exist. Studies put
# agents' estimates of their own work at 3-10x too long (see README).
PRIOR = {("own", "g"): (0.15, 0.5)}


def log_path():
    if os.environ.get("ETA_LOG"):
        return Path(os.environ["ETA_LOG"])
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "agent-eta" / "log.jsonl"


def now():
    fake = os.environ.get("ETA_NOW")  # for tests
    return (datetime.fromisoformat(fake) if fake else datetime.now()).astimezone()


def die(msg):
    sys.exit("eta: " + msg)


def at(s):
    """--at value -> datetime. HH:MM means the most recent such time."""
    t = now()
    if not s:
        return t
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        h, m = map(int, s.split(":"))
        x = t.replace(hour=h, minute=m, second=0, microsecond=0)
        return x - timedelta(days=1) if x > t else x
    try:
        return datetime.fromisoformat(s).astimezone()
    except ValueError:
        die(f"bad time '{s}' (use HH:MM or an ISO timestamp)")


def iso(t):
    return t.isoformat(timespec="seconds")


def dur(s):
    """'2h30m' -> 150.0 minutes."""
    s = s.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = re.findall(r"(\d+(?:\.\d+)?)([smhd])", s)
    if not parts or "".join(n + u for n, u in parts) != s:
        die(f"bad duration '{s}' (use 45s, 25m, 1.5h, 2h30m, 2d)")
    return sum(float(n) * {"s": 1 / 60, "m": 1, "h": 60, "d": 1440}[u] for n, u in parts)


def fmt(m):
    if m < 1:
        return f"{round(m * 60)}s"
    if m < 90:
        return f"{round(m)}m"
    m = int(round(m / 5) * 5)
    h, r = divmod(m, 60)
    if h >= 48:
        return f"{h // 24}d{h % 24}h" if h % 24 else f"{h // 24}d"
    return f"{h}h{r:02d}m" if r else f"{h}h"


def rng(lo, hi):
    a, b = fmt(lo), fmt(hi)
    return a if a == b else f"{a}-{b}"


def about(tri):
    lo, typ, hi = tri
    return f"about {fmt(typ)}" + ("" if fmt(lo) == fmt(hi) else f" ({rng(lo, hi)})")


def clock(t, ref):
    return t.strftime("%H:%M" if t.date() == ref.date() else "%a %H:%M")


def finish(ref, tri):
    lo, typ, hi = (ref + timedelta(minutes=x) for x in tri)
    out = f"around {clock(typ, ref)}"
    if clock(lo, ref) != clock(hi, ref):
        out += f" ({clock(lo, ref)}-{clock(hi, lo)})"  # name the weekday once when both ends share it
    return f"{out} {ref.strftime('%Z')}".strip()


def kind_ok(k):
    if not re.fullmatch(r"[a-z0-9_-]+(/[a-z0-9._-]+)?", k) or k.split("/")[0] not in KINDS:
        die(f"bad kind '{k}': use one of {', '.join(KINDS)}, optionally with /tag")
    return k


def est(s):
    """'LO-HI[:m]' -> (lo, hi, basis)."""
    basis = "g"
    if s[-2:] in (":m", ":g"):
        s, basis = s[:-2], s[-1]
    lo, _, hi = s.partition("-")
    lo, hi = dur(lo), dur(hi or lo)
    if hi < lo:
        die(f"range '{s}' is backwards")
    return lo, hi, basis


def events():
    p = log_path()
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def write(e):
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(e, separators=(",", ":")) + "\n")


def state():
    """Replay the log into open/closed plans and a flat list of past results."""
    plans, obs = {}, []
    for e in events():
        ev, i = e.get("ev"), e.get("id")
        if ev == "plan":
            plans[i] = dict(e, steps=[], prog=[], end=None, actual={})
        elif ev == "obs":
            obs.append(e)
        elif i in plans:
            if ev == "step":
                plans[i]["steps"].append((e["t"], e["kind"]))
            elif ev == "prog":
                plans[i]["prog"].append((e["t"], e["done"], e["total"]))
            elif ev == "drop":
                del plans[i]
            elif ev == "done":
                plans[i].update(end=e["t"], actual=e.get("actual", {}))
    for p in plans.values():
        for k, a in p["actual"].items():
            ps = [x for x in p["parts"] if x["kind"] == k and x.get("lo") is not None]
            o = {"kind": k, "actual": a, "label": p["label"]}
            if ps and len({x["basis"] for x in ps}) == 1:  # mixed measured+guess teaches nothing
                o.update(lo=sum(x["lo"] for x in ps), hi=sum(x["hi"] for x in ps), basis=ps[0]["basis"])
            obs.append(o)
    return plans, obs


def mid_of(lo, hi):
    return math.sqrt(lo * hi) if lo > 0 else hi / 2


def ratio(o):
    mid = mid_of(o["lo"], o["hi"])
    return o["actual"] / mid if mid else None


def spread(vals):
    """(low, typical, high) of past values: the extremes until there are 8, then p10-p90.
    A new result lands inside the extremes of n past ones about (n-1)/(n+1) of the time."""
    s = sorted(vals)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    if n < 8:
        return s[0], med, s[-1]

    def pct(p):
        i = p * (n - 1)
        return s[int(i)] + (s[min(int(i) + 1, n - 1)] - s[int(i)]) * (i - int(i))
    return pct(0.1), med, pct(0.9)


def pool(obs, kind, need, basis=None):
    """At least `need` past results for this kind, else for its top-level kind."""
    top = kind.split("/")[0]
    for name, match in ((kind, lambda k: k == kind), (top + "/*", lambda k: k.split("/")[0] == top)):
        sel = [o for o in obs if match(o["kind"]) and (basis is None or o.get("basis") == basis)]
        if len(sel) >= need:
            return name, sel
    return kind, []


def correct(obs, part):
    """Fill part's clo, typ, chi (low, most likely, high) and return the note explaining them."""
    k, lo, hi, basis = part["kind"], part.get("lo"), part.get("hi"), part["basis"]
    if lo is None:  # bare KIND: take the range from past real durations
        name, sel = pool(obs, k, 2)
        if not sel:
            die(f"fewer than 2 past results for {k}: give a range and tell the user it is unmeasured")
        part["clo"], part["typ"], part["chi"] = spread([o["actual"] for o in sel])
        return f"from {len(sel)} past {name} durations"
    mid = mid_of(lo, hi)
    name, sel = pool([o for o in obs if "lo" in o and ratio(o)], k, MIN_N, basis)
    if k.split("/")[0] == "human":
        f, note = None, "waits on the user, not in the total"
    elif sel:
        f = spread([ratio(o) for o in sel])
        note = f"history x{f[1]:.2g} (x{f[0]:.2g}-{f[2]:.2g}), n={len(sel)} {name}"
    elif (k.split("/")[0], basis) in PRIOR:
        a, b = PRIOR[(k.split("/")[0], basis)]
        f = (a, math.sqrt(a * b), b)
        note = f"prior x{a}-{b}: agents overestimate own work; no history yet"
    else:
        f, note = None, "measured, no history" if basis == "m" else "UNMEASURED GUESS, no history"
    # History replaces the stated width: past scatter says more than the range the agent wrote.
    part["clo"], part["typ"], part["chi"] = (mid * x for x in f) if f else (lo, mid, hi)
    return note


def tri(p):
    return p["clo"], p.get("typ", mid_of(p["clo"], p["chi"])), p["chi"]


def combine(tris):
    """Sum the most likely values; add the spreads in quadrature, because separate
    parts rarely all run long together. One dominant part keeps its full range."""
    t = sum(x[1] for x in tris)
    return (t - math.sqrt(sum((x[1] - x[0]) ** 2 for x in tris)), t,
            t + math.sqrt(sum((x[2] - x[1]) ** 2 for x in tris)))


def counted(parts):
    return [p for p in parts if p["kind"].split("/")[0] != "human"]


def cmd_plan(a):
    plans, obs = state()
    if not a.parts:
        die("give at least one part: KIND=LO-HI, or a bare KIND to use history")
    parts, notes = [], []
    for spec in a.parts:
        k, sep, v = spec.partition("=")
        p = {"kind": kind_ok(k), "basis": "g"}
        if sep:
            p["lo"], p["hi"], p["basis"] = est(v)
        notes.append(correct(obs, p))
        parts.append(p)
    c = counted(parts)
    if not c:
        die("every part waits on the user: nothing to time")
    t = now()
    i = max([e.get("id", 0) for e in events() if e.get("ev") == "plan"], default=0) + 1
    write({"ev": "plan", "id": i, "t": iso(t), "label": a.label, "parts": parts})
    print(f"#{i} {a.label} | now {clock(t, t)} {t.strftime('%Z')}")
    w = max(len(p["kind"]) for p in parts)
    for p, n in zip(parts, notes):
        raw = "(history)" if p.get("lo") is None else rng(p["lo"], p["hi"]) + (":m" if p["basis"] == "m" else "")
        print(f"{p['kind']:<{w}}  {raw:<11} -> {about(tri(p)):<26} {n}")
    total = combine([tri(p) for p in c])
    print(f"TOTAL {about(total)} | finish {finish(t, total)}")
    # If one part causes most of the spread, show the estimate without it so it can be quoted apart.
    var = [(tri(p)[2] - tri(p)[0]) ** 2 for p in c]
    big = max(range(len(c)), key=lambda j: var[j])
    if (len(c) > 1 and var[big] >= 0.5 * sum(var) and math.sqrt(var[big]) >= 0.25 * total[1]
            and c[big]["chi"] >= 2 * c[big]["clo"]):
        rest = combine([tri(p) for j, p in enumerate(c) if j != big])
        print(f"SPREAD comes mostly from {c[big]['kind']}. Without it: {about(rest)}. Quote that part apart.")


def get(plans, i, want_open=True):
    if i not in plans:
        die(f"no estimate #{i}")
    if want_open and plans[i]["end"]:
        die(f"#{i} is already closed")
    return plans[i]


def position(p):
    """Index of the part now running, its kind, and when it started."""
    idx, since = 0, p["t"]
    kinds = [x["kind"] for x in p["parts"]]
    for t, k in p["steps"]:
        later = [j for j, kk in enumerate(kinds) if kk == k and j > idx]
        idx = later[0] if later else (kinds.index(k) if k in kinds else None)
        since = t
        if idx is None:  # a part that was never planned
            return None, k, since
    return idx, kinds[idx], since


def mins(a, b):
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60


def last_mark(p):
    return p["steps"][-1][0] if p["steps"] else p["t"]


def cmd_step(a):
    plans, _ = state()
    p = get(plans, a.id)
    k = kind_ok(a.kind)
    t = at(a.at)
    if mins(last_mark(p), iso(t)) < 0:
        die("--at is before the previous mark")
    write({"ev": "step", "id": a.id, "t": iso(t), "kind": k})
    planned = "" if k in [x["kind"] for x in p["parts"]] else " (not in the plan: unplanned time)"
    print(f"#{a.id} {k} started {clock(t, now())}{planned}")


def cmd_check(a):
    plans, _ = state()
    t = now()
    ts = iso(t)
    if a.id is None:
        open_ = [p for p in plans.values() if not p["end"]]
        for p in open_:
            print(f"#{p['id']} {p['label']} | open for {fmt(mins(p['t'], ts))} | in {position(p)[1]}")
        if not open_:
            print("no open estimates")
        return
    p = get(plans, a.id)
    idx, k, since = position(p)
    e = mins(since, ts)
    print(f"#{p['id']} {p['label']} | now {clock(t, t)} {t.strftime('%Z')} | elapsed {fmt(mins(p['t'], ts))}")
    later = [tri(x) for x in counted(p["parts"][idx + 1:])] if idx is not None else []
    cur = tri(p["parts"][idx]) if idx is not None else None
    left = None
    if a.done is not None:
        if not a.total or not 0 < a.done <= a.total:
            die("need 0 < --done <= --total")
        prev = [x for x in p["prog"] if x[0] >= since and x[2] == a.total and x[1] < a.done]
        write({"ev": "prog", "id": a.id, "t": ts, "done": a.done, "total": a.total})
        if prev:  # the speed between two readings leaves out startup and tracks the current pace
            t0, d0, _ = prev[-1]
            x = mins(t0, ts) * (a.total - a.done) / (a.done - d0)
            left, how = (x * 0.9, x, x * 1.15), f"rate since the reading at {clock(datetime.fromisoformat(t0), t)}"
        else:  # from the start of the part: includes startup, and early progress is a weak guide
            x, f = e * (a.total - a.done) / a.done, 1 - a.done / a.total
            left = (x * (1 - 0.15 * f), x, x * (1 + 0.3 * f))
            how = "rate since this part started; a second reading later gives a tighter figure"
        print(f"{k}: {a.done:g}/{a.total:g} -> {about(left)} more for this part ({how})")
    elif cur is None:
        print(f"{k}: {fmt(e)} so far, unplanned part, no estimate")
    elif e > cur[2]:
        print(f"OVER: {k} at {fmt(e)}, estimate was {about(cur)}. No progress given, so the time left "
              f"is unknown: read the job's progress and pass --done/--total, or check for a hang.")
    else:
        lo, hi = max(0, cur[0] - e), cur[2] - e
        left = (lo, cur[1] - e if cur[1] - e > lo else (lo + hi) / 2, hi)
        print(f"{k}: {fmt(e)} of {about(cur)} (no progress given: this only restates the estimate)")
    if left:
        total = combine([left] + later)
        print(f"remaining {about(total)} | finish {finish(t, total)}")
    elif later:
        print(f"after this part: {about(combine(later))} more")


def cmd_done(a):
    plans, _ = state()
    p = get(plans, a.id)
    t = iso(at(a.at))
    if mins(last_mark(p), t) < 0:
        die("--at is before the previous mark")
    total = mins(p["t"], t)
    kinds = {x["kind"] for x in p["parts"]}
    actual = {}
    if p["steps"] or len(kinds) == 1:  # per-part times are known from the clock
        marks = [(p["t"], p["parts"][0]["kind"])] + p["steps"] + [(t, None)]
        for (t0, k), (t1, _) in zip(marks, marks[1:]):
            actual[k] = actual.get(k, 0) + mins(t0, t1)
    for spec in a.actuals:
        k, sep, v = spec.partition("=")
        if not sep:
            die(f"bad actual '{spec}': use KIND=DURATION")
        actual[kind_ok(k)] = dur(v)
    actual = {k: round(v, 1) for k, v in actual.items()}
    write({"ev": "done", "id": a.id, "t": t, "total": round(total, 1), "actual": actual})
    q = combine([tri(x) for x in counted(p["parts"])])
    verdict = f"LATE by {fmt(total - q[2])}" if total > q[2] else f"EARLY by {fmt(q[0] - total)}" if total < q[0] else "in range"
    print(f"#{a.id} done in {fmt(total)}, quoted {about(q)}: {verdict}")
    for k, v in actual.items():
        ps = [x for x in p["parts"] if x["kind"] == k and x.get("lo") is not None]
        if ps:
            o = {"lo": sum(x["lo"] for x in ps), "hi": sum(x["hi"] for x in ps), "actual": v}
            r = ratio(o)  # shown even for mixed measured+guess parts, which the log does not learn from
            print(f"{k}  {fmt(v)} vs raw {rng(o['lo'], o['hi'])}" + (f"  x{r:.2g}" if r else ""))
        else:
            print(f"{k}  {fmt(v)}" + ("" if k in kinds else "  UNPLANNED"))
    if not actual:
        print("per-part times unknown (no step calls): pass KIND=DURATION to teach the log")


def cmd_drop(a):
    plans, _ = state()
    get(plans, a.id)
    write({"ev": "drop", "id": a.id, "t": iso(now())})
    print(f"#{a.id} dropped")


def cmd_add(a):
    e = {"ev": "obs", "t": a.date or iso(now()),
         "kind": kind_ok(a.kind), "actual": dur(a.actual), "label": a.label or ""}
    if a.estimate:
        e["lo"], e["hi"], e["basis"] = est(a.estimate)
    write(e)
    print(f"added {e['kind']} {fmt(e['actual'])}" + (f" vs {rng(e['lo'], e['hi'])}" if a.estimate else ""))


def cmd_stats(a):
    _, obs = state()
    if a.kind:
        obs = [o for o in obs if o["kind"] == a.kind or o["kind"].split("/")[0] == a.kind]
    groups = {}
    for o in obs:
        groups.setdefault((o["kind"], o.get("basis", "-")), []).append(o)
    if not groups:
        print("no history yet")
        return
    w = max(len(k) for k, _ in groups)
    print(f"{'kind':<{w}}  est       n  real/estimate         real durations")
    for (k, b), sel in sorted(groups.items()):
        rs = sorted(r for r in (ratio(o) for o in sel if "lo" in o) if r)
        acts = [o["actual"] for o in sel]
        if not rs:
            rtxt = "-"
        elif len(rs) == 1:
            rtxt = f"x{rs[0]:.2g}"
        else:
            rtxt = f"x{rs[0]:.2g}-{rs[-1]:.2g} median {spread(rs)[1]:.2g}"
        basis = {"m": "measured", "g": "guess", "-": "none"}[b]
        print(f"{k:<{w}}  {basis:<8} {len(sel):>2}  {rtxt:<20}  {rng(min(acts), max(acts))}")
    print(f"real/estimate above 1 means it took longer than estimated. History corrects new estimates from n={MIN_N}.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="eta.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan"); s.add_argument("label"); s.add_argument("parts", nargs="*"); s.set_defaults(f=cmd_plan)
    s = sub.add_parser("step"); s.add_argument("id", type=int); s.add_argument("kind"); s.add_argument("--at"); s.set_defaults(f=cmd_step)
    s = sub.add_parser("check"); s.add_argument("id", type=int, nargs="?")
    s.add_argument("--done", type=float); s.add_argument("--total", type=float); s.set_defaults(f=cmd_check)
    s = sub.add_parser("done"); s.add_argument("id", type=int); s.add_argument("actuals", nargs="*"); s.add_argument("--at"); s.set_defaults(f=cmd_done)
    s = sub.add_parser("drop"); s.add_argument("id", type=int); s.set_defaults(f=cmd_drop)
    s = sub.add_parser("stats"); s.add_argument("kind", nargs="?"); s.set_defaults(f=cmd_stats)
    s = sub.add_parser("add"); s.add_argument("kind"); s.add_argument("actual"); s.add_argument("estimate", nargs="?")
    s.add_argument("-l", "--label"); s.add_argument("-d", "--date"); s.set_defaults(f=cmd_add)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
