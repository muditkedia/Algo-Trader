"""Scheduling layer - the batch data jobs (full import, daily, intraday).

These are callable, testable job definitions over the ingestion engine, not a
running daemon: a live loop belongs to the paper-trading phase (it needs a live
feed and is explicitly out of Phase-2 scope). Wire these to cron / Task
Scheduler / the schedule skill at deployment.
"""

from algo.schedule.jobs import DataJobs

__all__ = ["DataJobs"]
