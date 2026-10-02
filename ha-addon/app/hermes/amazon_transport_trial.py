"""Bounded crossover measurements on scheduled Amazon reads; never extra traffic."""

import hashlib
import json
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .storage import load_json, save_json
from .utils import parse_iso_datetime

CONTROL_PATH = Path("/data/amazon_transport_trial.json")
RESULTS_PATH = Path("/data/amazon_transport_trial_results.json")
MAX_SAMPLES = 20000
MODES = ("browser", "http", "http", "browser")


def active_trial(now=None):
    now = now or datetime.now(timezone.utc)
    control = load_json(CONTROL_PATH, {})
    if not isinstance(control, dict) or not control.get("id"):
        return None
    start, end = parse_iso_datetime(control.get("started_at")), parse_iso_datetime(control.get("ends_at"))
    if not start or not end or not start <= now < end:
        return None
    phase = int((now - start).total_seconds() // 3600)
    return {**control, "phase": phase, "transport": "browser" if control.get("purpose") == "browser_validation" else MODES[phase % len(MODES)]}


def start_trial(now=None, purpose="reader_comparison"):
    now = now or datetime.now(timezone.utc)
    existing = active_trial(now)
    if existing:
        return existing
    # Retain the completed comparison when beginning a new validation run.
    completed = load_json(RESULTS_PATH, {})
    if isinstance(completed, dict) and len(str(completed.get("id", ""))) == 32:
        saved_id = str(completed["id"])
        if all(char in "0123456789abcdef" for char in saved_id):
            save_json(RESULTS_PATH.with_name(f"amazon_trial_archive_{saved_id}.json"), completed)
    control = {"id": uuid.uuid4().hex, "started_at": now.isoformat(), "purpose": purpose,
               "ends_at": (now + timedelta(hours=1 if purpose == "browser_validation" else 24)).isoformat()}
    save_json(CONTROL_PATH, control)
    return control


def stop_trial(now=None):
    control = load_json(CONTROL_PATH, {})
    if isinstance(control, dict) and control.get("id"):
        control["ends_at"] = (now or datetime.now(timezone.utc)).isoformat()
        save_json(CONTROL_PATH, control)


def watch_signature(watch, config):
    # No credentials, HTML or cookie data enter the measurements.
    values = {field: getattr(watch, field, None) for field in
              ("url", "name", "size", "include_variations", "official_seller_only", "excluded_terms",
               "priority", "target_price", "minimum_price", "tracking_id", "check_interval_minutes")}
    values.update({field: getattr(config, field, None) for field in
                   ("interval_seconds", "request_delay_min_seconds", "request_delay_max_seconds", "request_timeout_seconds")})
    values["scope"] = [str(item) for item in config.watches]
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:24]


def append_cycle(trial, samples, cycle_seconds):
    results = load_json(RESULTS_PATH, {})
    if not isinstance(results, dict) or results.get("id") != trial["id"]:
        results = {"id": trial["id"], "started_at": trial["started_at"], "ends_at": trial["ends_at"],
                   "samples": [], "cycles": [], "discarded_samples": 0}
    combined = results["samples"] + samples
    results["discarded_samples"] += max(0, len(combined) - MAX_SAMPLES)
    results["samples"] = combined[-MAX_SAMPLES:]
    results["cycles"] = (results["cycles"] + [{"at": datetime.now(timezone.utc).isoformat(),
        "transport": trial["transport"], "phase": trial["phase"], "seconds": round(cycle_seconds, 3)}])[-3000:]
    results["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(RESULTS_PATH, results)


def summarize(results):
    samples = results.get("samples", []) if isinstance(results, dict) else []
    groups = {}
    for item in samples:
        if item.get("network_attempts", 0) > 0:
            groups.setdefault((item["watch"], item["config"]), set()).add(item["transport"])
    matched = {key for key, modes in groups.items() if modes == {"http", "browser"}}
    modes = {}
    for mode in ("http", "browser"):
        rows = [item for item in samples if item["transport"] == mode and item.get("network_attempts", 0) > 0
                and (item["watch"], item["config"]) in matched]
        modes[mode] = {
            "reads": len(rows), "network_attempts": sum(item["network_attempts"] for item in rows),
            "captcha": sum(item["captcha"] for item in rows), "http_503": sum(item["http_503"] for item in rows),
            "priced": sum(item["outcome"] in {"priced", "partial"} for item in rows),
            "empty": sum(item["outcome"] == "unavailable" for item in rows),
            "errors": sum(item["outcome"] == "error" for item in rows),
            "warehouse_offers": sum(item["warehouse_count"] for item in rows),
            "warehouse_reads": sum(item["warehouse_count"] > 0 for item in rows),
            "median_variants": statistics.median([item["variant_count"] for item in rows]) if rows else 0,
            "median_seconds": round(statistics.median([item["seconds"] for item in rows]), 2) if rows else 0,
            "actual_transports": {transport: sum(item.get("transports", {}).get(transport, 0) for item in rows)
                                  for transport in ("curl_chrome", "requests", "browser")},
        }
    cards = []
    for watch, signature in sorted(matched):
        rows = [item for item in samples if item["watch"] == watch and item["config"] == signature
                and item.get("network_attempts", 0) > 0]
        cards.append({"name": rows[-1]["name"], "watch": watch, "config": signature,
            "modes": {mode: {"reads": sum(item["transport"] == mode for item in rows),
                "network_attempts": sum(item["network_attempts"] for item in rows if item["transport"] == mode),
                "captcha": sum(item["captcha"] for item in rows if item["transport"] == mode),
                "http_503": sum(item["http_503"] for item in rows if item["transport"] == mode),
                "priced": sum(item["outcome"] in {"priced", "partial"} for item in rows if item["transport"] == mode),
                "median_variants": statistics.median([item["variant_count"] for item in rows if item["transport"] == mode]),
                "warehouse_reads": sum(item["warehouse_count"] > 0 for item in rows if item["transport"] == mode),
                "median_seconds": round(statistics.median([item["seconds"] for item in rows if item["transport"] == mode]), 2),
            } for mode in ("http", "browser")}})
    validation = []
    for watch, signature in sorted(groups):
        rows = [item for item in samples if item["watch"] == watch and item["config"] == signature
                and item["transport"] == "browser" and item.get("network_attempts", 0) > 0]
        if not rows:
            continue
        audits = [audit for row in rows for audit in row.get("browser_audits", [])]
        validation.append({"watch": watch, "config": signature, "name": rows[-1]["name"],
            "reads": len(rows), "network_attempts": sum(item["network_attempts"] for item in rows),
            "captcha": sum(item["captcha"] for item in rows), "http_503": sum(item["http_503"] for item in rows),
            "priced": sum(item["outcome"] in {"priced", "partial"} for item in rows),
            "warehouse_reads": sum(item["warehouse_count"] > 0 for item in rows),
            "median_seconds": round(statistics.median([item["seconds"] for item in rows]), 2),
            "audits": len(audits), "late_data": sum(bool(item["changed"]) for item in audits),
            "coverage_details": audits[-60:]})
    return {"matched_cards": len(matched), "modes": modes, "cards": cards, "browser_validation": validation,
            "protection_waits": sum(item["outcome"] == "protection_wait" for item in samples),
            "unmatched_reads": sum(item.get("network_attempts", 0) > 0 and (item["watch"], item["config"]) not in matched
                                   for item in samples),
            "discarded_samples": results.get("discarded_samples", 0),
            "total_samples": len(samples)}
