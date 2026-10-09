"""Durable incidents, bounded recovery and resource measurements without secrets."""

import json
import time
import uuid

from .constants import APP_VERSION
from .database import Database
from .logging_utils import redact


def _clean_context(value):
    if isinstance(value, dict):
        return {redact(str(key)): _clean_context(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_context(item) for item in value]
    return redact(value) if isinstance(value, str) else value


class Diagnostics:
    def __init__(self, path):
        self.db = Database.at(path)
        self.progress: dict[str, float] = {}

    def interrupt_abandoned(self):
        self.db.transaction([("UPDATE jobs SET finished=?,outcome='interrupted',detail=? WHERE finished IS NULL",
            (time.time(), "Uygulama önceki iş tamamlanmadan kapanmış; kalıcı sonuçlar korundu."))])

    def start(self, site, key, planned=None):
        job = uuid.uuid4().hex
        now = time.time()
        self.progress[site] = time.monotonic()
        planned = planned if planned is not None else now
        self.db.transaction([("INSERT INTO jobs(id,site,watch_key,version,started,planned,delay_ms) VALUES (?,?,?,?,?,?,?)",
                              (job, site, key, APP_VERSION, now, planned, round(max(0, now-planned)*1000)))])
        return job

    def finish(self, job, site, outcome, duration_ms, detail="", evidence=None):
        self.progress[site] = time.monotonic()
        self.db.transaction([("UPDATE jobs SET finished=?,outcome=?,duration_ms=?,detail=?,evidence=? WHERE id=?",
                              (time.time(), outcome, duration_ms, redact(str(detail))[:2000],
                               redact(json.dumps(evidence or {}, ensure_ascii=False)), job))])

    def incident(self, component, kind, detail, recovery="", context=None):
        identity = f"{component}:{kind}"
        now = time.time()
        self.db.transaction([("INSERT INTO incidents(id,component,kind,detail,opened,updated,recovery,context) VALUES (?,?,?,?,?,?,?,?) "
                              "ON CONFLICT(id) DO UPDATE SET detail=excluded.detail,updated=excluded.updated,"
                              "count=CASE WHEN incidents.resolved IS NULL THEN incidents.count+1 ELSE 1 END,"
                              "opened=CASE WHEN incidents.resolved IS NULL THEN incidents.opened ELSE excluded.opened END,"
                              "resolved=NULL,recovery=excluded.recovery,context=excluded.context",
                              (identity, component, kind, redact(str(detail))[:2000], now, now, recovery,
                               json.dumps(_clean_context(context or {}), ensure_ascii=False)))])

    def recover(self, component, recovery):
        self.db.transaction([("UPDATE incidents SET resolved=?,recovery=? WHERE component=? AND resolved IS NULL",
                              (time.time(), recovery, component))])

    def active(self):
        with self.db.lock:
            rows = self.db.connect().execute(
                "SELECT id,component,kind,detail,opened,updated,count,recovery,context FROM incidents WHERE resolved IS NULL "
                "ORDER BY updated DESC").fetchall()
        fields = ("id", "component", "kind", "detail", "opened", "updated", "count", "recovery", "context")
        items = [dict(zip(fields, row)) for row in rows]
        for item in items:
            try:
                context = json.loads(item["context"])
            except (ValueError, TypeError):
                context = {}
            item["context"] = context if isinstance(context, dict) else {}
        return items

    def prune(self):
        cutoff = time.time() - 30 * 86400
        self.db.transaction([("DELETE FROM jobs WHERE finished < ?", (cutoff,)),
                             ("DELETE FROM incidents WHERE resolved < ?", (cutoff,))])


def runtime_metrics(path):
    """Counts and timing only; never options, URLs, messages or credentials."""
    db = Database.at(path)
    with db.lock:
        connection = db.connect()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {"price_points": connection.execute("SELECT count(*) FROM prices").fetchone()[0] if "prices" in tables else 0,
                  "reads": connection.execute("SELECT count(*) FROM reads").fetchone()[0] if "reads" in tables else 0,
                  "jobs": connection.execute("SELECT count(*) FROM jobs").fetchone()[0],
                  "completed_jobs": connection.execute("SELECT count(*) FROM jobs WHERE finished IS NOT NULL").fetchone()[0],
                  "open_incidents": connection.execute("SELECT count(*) FROM incidents WHERE resolved IS NULL").fetchone()[0],
                  "outbox": dict(connection.execute("SELECT state,count(*) FROM outbox GROUP BY state").fetchall()),
                  "state_entries": 0}
        migration = connection.execute("SELECT value FROM meta WHERE key='v4_migration'").fetchone() if "meta" in tables else None
        result["migration"] = json.loads(migration[0]) if migration else None
        row = connection.execute("SELECT payload FROM snapshots WHERE name='state.json'").fetchone()
        if row:
            result["state_entries"] = sum(1 for key in json.loads(row[0]) if key != "_meta")
        latency = [row[0] for row in connection.execute(
            "SELECT delivered-created FROM outbox WHERE state='sent' AND delivered IS NOT NULL "
            "ORDER BY delivered DESC LIMIT 1000")]
        delays = [row[0]/1000 for row in connection.execute("SELECT delay_ms FROM jobs WHERE finished IS NOT NULL ORDER BY started DESC LIMIT 1000")]
        latest = connection.execute("SELECT site,max(finished) FROM jobs GROUP BY site").fetchall()
    def percentile(values, ratio):
        return round(sorted(values)[min(len(values)-1, int(len(values)*ratio))], 3) if values else None
    result["delivery_seconds"] = {"samples": len(latency), "p50": percentile(latency, .50), "p95": percentile(latency, .95)}
    result["schedule_delay_seconds"] = {"samples": len(delays), "p50": percentile(delays, .50), "p95": percentile(delays, .95)}
    result["last_site_read_age_seconds"] = {site: round(max(0, time.time()-at)) if at else None for site, at in latest}
    return result
