"""Run with: python3 -m unittest discover -s tests"""
import contextlib
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import eta  # noqa: E402


class EtaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ETA_LOG"] = str(Path(self.tmp.name) / "log.jsonl")
        os.environ["TZ"] = "UTC"  # the script prints local clock times
        if hasattr(time, "tzset"):
            time.tzset()
        self.at("2026-01-05T10:00:00+00:00")

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("ETA_LOG", None)
        os.environ.pop("ETA_NOW", None)

    def at(self, iso):
        os.environ["ETA_NOW"] = iso

    def run_eta(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            eta.main(list(argv))
        return out.getvalue()

    def test_durations(self):
        self.assertEqual(eta.dur("2h30m"), 150)
        self.assertEqual(eta.dur("90"), 90)
        self.assertEqual(eta.dur("45s"), 0.75)
        self.assertEqual(eta.dur("1.5h"), 90)
        with self.assertRaises(SystemExit):
            eta.dur("3x")
        self.assertEqual(eta.fmt(0.5), "30s")
        self.assertEqual(eta.fmt(150), "2h30m")
        self.assertEqual(eta.fmt(2880), "2d")

    def test_unmeasured_guess_is_flagged_and_left_alone(self):
        out = self.run_eta("plan", "job", "compute=20m-30m")
        self.assertIn("UNMEASURED GUESS", out)
        self.assertIn("TOTAL about 24m (20m-30m)", out)

    def test_own_guess_gets_prior_until_history_exists(self):
        out = self.run_eta("plan", "task", "own=60m-120m")
        self.assertIn("prior", out)
        self.assertIn("TOTAL about 23m (13m-42m)", out)
        # a measured own-work estimate is not shrunk
        self.assertIn("TOTAL about 85m (60m-2h)", self.run_eta("plan", "task", "own=60m-120m:m"))

    def test_history_corrects_after_three_results(self):
        for _ in range(3):
            self.run_eta("add", "remote/ci", "30m", "10m-20m")  # real/estimate = 2.1
        out = self.run_eta("plan", "job", "remote/ci=10m-20m")
        self.assertIn("history x2.1 (x2.1-2.1), n=3 remote/ci", out)
        self.assertIn("TOTAL about 30m |", out)
        # another tag under the same kind falls back to the parent pool
        self.assertIn("remote/*", self.run_eta("plan", "job", "remote/other=10m-20m"))
        # measured estimates have their own history and are not touched by guesses
        self.assertIn("measured, no history", self.run_eta("plan", "job", "remote/ci=10m-20m:m"))

    def test_history_range_comes_from_past_scatter(self):
        for real in ("20m", "30m", "40m"):
            self.run_eta("add", "compute", real, "20m")  # ratios 1, 1.5, 2
        out = self.run_eta("plan", "job", "compute=10m")
        self.assertIn("about 15m (10m-20m)", out)

    def test_bare_kind_uses_past_durations(self):
        with self.assertRaises(SystemExit):
            self.run_eta("plan", "job", "queue/ci")
        self.run_eta("add", "queue/ci", "5m")
        self.run_eta("add", "queue/ci", "41m")
        self.assertIn("TOTAL about 23m (5m-41m)", self.run_eta("plan", "job", "queue/ci"))

    def test_parts_combine_without_stacking_every_worst_case(self):
        out = self.run_eta("plan", "job", "compute=20m-30m:m", "transfer=20m-30m:m")
        # adding the lows and the highs would give 40m-60m
        self.assertIn("TOTAL about 49m (43m-57m)", out)

    def test_wide_part_is_called_out(self):
        self.run_eta("add", "queue/ci", "10m")
        self.run_eta("add", "queue/ci", "2h")
        out = self.run_eta("plan", "job", "queue/ci", "compute=60m-70m:m")
        self.assertIn("SPREAD comes mostly from queue/ci. Without it: about 65m (60m-70m)", out)
        self.assertNotIn("SPREAD", self.run_eta("plan", "job", "compute=60m-70m:m", "transfer=5m:m"))

    def test_human_part_is_not_in_the_total(self):
        out = self.run_eta("plan", "job", "compute=10m:m", "human=30m")
        self.assertIn("TOTAL about 10m", out)
        self.assertIn("finish around 10:10", out)
        with self.assertRaises(SystemExit):
            self.run_eta("plan", "job", "human=30m")

    def test_steps_time_each_part_and_done_teaches_the_log(self):
        self.run_eta("plan", "job", "queue=0-10m", "compute=20m-30m:m")
        self.at("2026-01-05T10:07:00+00:00")
        self.run_eta("step", "1", "compute")
        self.at("2026-01-05T10:47:00+00:00")
        out = self.run_eta("done", "1")
        self.assertIn("done in 47m, quoted about 29m (23m-37m): LATE by 10m", out)
        self.assertIn("compute  40m vs raw 20m-30m", out)
        stats = self.run_eta("stats")
        self.assertIn("compute", stats)
        self.assertIn("queue", stats)

    def test_at_records_a_change_noticed_late(self):
        self.run_eta("plan", "job", "queue=0-10m", "compute=20m-30m:m")
        self.at("2026-01-05T11:30:00+00:00")
        self.run_eta("step", "1", "compute", "--at", "10:07")
        with self.assertRaises(SystemExit):
            self.run_eta("done", "1", "--at", "10:05")  # before the previous mark
        out = self.run_eta("done", "1", "--at", "2026-01-05T10:47:00+00:00")
        self.assertIn("done in 47m", out)
        self.assertIn("compute  40m", out)

    def test_check_projects_from_progress_and_refuses_to_guess_when_over(self):
        self.run_eta("plan", "job", "compute=20m-30m:m", "transfer=5m")
        self.at("2026-01-05T10:10:00+00:00")
        out = self.run_eta("check", "1", "--done", "2", "--total", "10")
        self.assertIn("2/10 -> about 40m (35m-50m) more", out)  # 40m at the measured rate
        self.assertIn("remaining about 45m (40m-55m)", out)
        # a second reading uses the speed between readings: 2 units in 10m, 6 left
        self.at("2026-01-05T10:20:00+00:00")
        out = self.run_eta("check", "1", "--done", "4", "--total", "10")
        self.assertIn("4/10 -> about 30m", out)
        self.assertIn("rate since the reading at 10:10", out)
        self.at("2026-01-05T11:00:00+00:00")
        out = self.run_eta("check", "1")
        self.assertIn("OVER", out)
        self.assertNotIn("remaining", out)

    def test_unplanned_part_is_reported(self):
        self.run_eta("plan", "job", "compute=20m:m")
        self.at("2026-01-05T10:20:00+00:00")
        self.assertIn("not in the plan", self.run_eta("step", "1", "own"))
        self.at("2026-01-05T10:50:00+00:00")
        self.assertIn("own  30m  UNPLANNED", self.run_eta("done", "1"))

    def test_drop_and_closed_ids(self):
        self.run_eta("plan", "job", "compute=20m:m")
        self.run_eta("drop", "1")
        self.assertIn("no open estimates", self.run_eta("check"))
        self.assertIn("no history yet", self.run_eta("stats"))
        with self.assertRaises(SystemExit):
            self.run_eta("done", "1")

    def test_plans_from_older_versions_still_read(self):
        Path(os.environ["ETA_LOG"]).write_text(
            '{"ev":"plan","id":1,"t":"2026-01-05T10:00:00+00:00","label":"old",'
            '"parts":[{"kind":"compute","basis":"m","lo":20,"hi":45,"clo":20,"chi":45}]}\n')
        self.assertIn("about 30m (20m-45m)", self.run_eta("check", "1"))

    def test_finish_time_names_the_day_when_it_crosses_midnight(self):
        self.at("2026-01-05T23:00:00+00:00")
        out = self.run_eta("plan", "job", "compute=30m-2h:m")
        self.assertIn("finish around Tue 00:00 (23:30-Tue 01:00)", out)


if __name__ == "__main__":
    unittest.main()
