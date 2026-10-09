"""Hermes data in Home Assistant: sensors after each cycle, an event per opportunity.

Calls go through the Supervisor proxy (`homeassistant_api: true`). Home
Assistant being unreachable never affects monitoring; failures are logged once
until the connection works again.
"""

import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from .logging_utils import log
from .models import OfferResult, PriceSummaryRow, WatchRule
from .utils import PROCESS_STARTED_AT, format_tl, parse_iso_datetime, site_label

CORE_API_URL = "http://supervisor/core/api"
OPPORTUNITY_EVENT = "hermes_firsat"
SENSOR_OPPORTUNITIES = "sensor.hermes_firsat_sayisi"
SENSOR_LAST_CYCLE = "sensor.hermes_son_tur"
SENSOR_ERRORS = "sensor.hermes_hata_sayisi"
# Keep entity attributes small; Home Assistant's recorder warns above ~16 KB.
MAX_LISTED_ITEMS = 25
# Idle cycles can finish every few seconds; every state change is written to
# Home Assistant's database, so the last-cycle sensor is refreshed at most
# this often and unchanged sensors are not sent again.
LAST_CYCLE_MIN_INTERVAL_SECONDS = 60


def _number(value) -> float:
    return float(round(value, 2))


class HomeAssistantBridge:
    def __init__(self, token: Optional[str] = None, base_url: str = CORE_API_URL, timeout: int = 5) -> None:
        self.token = (token if token is not None else os.getenv("SUPERVISOR_TOKEN", "")).strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._failing = False
        self._lock = threading.Lock()
        self._published: Dict[str, Any] = {}
        self._last_cycle_sent_at: Optional[datetime] = None

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _post(self, path: str, payload: Dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        try:
            response = requests.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout,
                                     headers={"Authorization": f"Bearer {self.token}"})
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - Home Assistant is optional for monitoring
            with self._lock:
                if not self._failing:
                    log(f"Home Assistant'a veri gönderilemedi (tekrar denenecek): {path} | {exc}")
                self._failing = True
            return False
        with self._lock:
            if self._failing:
                log("Home Assistant bağlantısı yeniden çalışıyor.")
            self._failing = False
        return True

    def set_state(self, entity_id: str, state: Any, attributes: Dict[str, Any]) -> bool:
        return self._post(f"/states/{entity_id}", {"state": state, "attributes": attributes})

    def _set_if_changed(self, entity_id: str, state: Any, attributes: Dict[str, Any]) -> None:
        payload = (state, attributes)
        if self._published.get(entity_id) == payload:
            return
        if self.set_state(entity_id, state, attributes):
            self._published[entity_id] = payload

    def fire_event(self, event_type: str, data: Dict[str, Any]) -> bool:
        return self._post(f"/events/{event_type}", data)

    # -- Hermes data ---------------------------------------------------------------

    def persistent_problem(self, identity, message):
        return self._post("/services/persistent_notification/create", {
            "notification_id": "hermes_" + identity.replace(":", "_"),
            "title": "Hermes takip uyarısı", "message": message})

    def clear_problem(self, identity):
        return self._post("/services/persistent_notification/dismiss", {
            "notification_id": "hermes_" + identity.replace(":", "_")})

    def publish_opportunity(self, watch: WatchRule, offer: OfferResult, title: str, url: str) -> bool:
        return self.fire_event(OPPORTUNITY_EVENT, {
            "site": site_label(watch.site),
            "takip": watch.name or title,
            "urun": title,
            "fiyat": _number(offer.price),
            "fiyat_metni": format_tl(offer.price, with_currency=True),
            "hedef": _number(watch.target_price),
            "fark": _number(offer.price - watch.target_price),
            "depo": bool(offer.is_warehouse),
            "satici": offer.seller or "",
            "url": url,
        })

    def publish_cycle(self, rows: List[PriceSummaryRow], stock_count: int, state: Dict[str, Any],
                      cycle_seconds: float, scan_seconds: float, finished_at: Optional[datetime] = None) -> None:
        finished_at = finished_at or datetime.now().astimezone()
        opportunities = sorted((row for row in rows if row.price <= row.target_price), key=lambda row: row.difference)
        self._set_if_changed(SENSOR_OPPORTUNITIES, len(opportunities), {
            "friendly_name": "Hermes fırsat sayısı",
            "icon": "mdi:tag-heart",
            "unit_of_measurement": "ürün",
            "state_class": "measurement",
            "firsatlar": [
                {"site": row.seller, "urun": row.product_title, "fiyat": _number(row.price), "hedef": _number(row.target_price),
                 "fark": _number(row.difference), "depo": row.is_warehouse, "url": row.product_url}
                for row in opportunities[:MAX_LISTED_ITEMS]
            ],
        })
        last_sent = self._last_cycle_sent_at
        if last_sent is None or (finished_at - last_sent).total_seconds() >= LAST_CYCLE_MIN_INTERVAL_SECONDS:
            if self.set_state(SENSOR_LAST_CYCLE, finished_at.isoformat(timespec="seconds"), {
                "friendly_name": "Hermes son tur",
                "icon": "mdi:timer-check-outline",
                "device_class": "timestamp",
                "sure_saniye": round(cycle_seconds),
                "tarama_saniye": round(scan_seconds),
                "urun_sayisi": len(rows),
                "stokta_olmayan": stock_count,
            }):
                self._last_cycle_sent_at = finished_at
        errors = recent_errors(state, finished_at)
        self._set_if_changed(SENSOR_ERRORS, len(errors), {
            "friendly_name": "Hermes hata sayısı",
            "icon": "mdi:alert-circle-outline",
            "unit_of_measurement": "hata",
            "state_class": "measurement",
            "hatalar": errors[:MAX_LISTED_ITEMS],
        })


def recent_errors(state: Dict[str, Any], now: datetime, hours: int = 24) -> List[Dict[str, str]]:
    """Watches whose latest read failed within the last day and since Hermes started (as on the dashboard)."""
    cutoff = max(now - timedelta(hours=hours), PROCESS_STARTED_AT)
    errors = []
    for key, entry in state.items():
        if key == "_meta" or not isinstance(entry, dict) or not entry.get("last_error"):
            continue
        checked_at = parse_iso_datetime(entry.get("last_checked_at"))
        if checked_at and checked_at >= cutoff:
            errors.append({
                "site": site_label(str(entry.get("site") or "")),
                "takip": str(entry.get("watch_name") or entry.get("configured_url") or ""),
                "hata": str(entry["last_error"])[:200],
            })
    return errors
