"""Price alerts: above / below a price, or a move of at least X % (vs the asset's own change basis).

Checked on every price update. Only fresh data can trigger (LIVE or DELAYED, never STALE or restored
prices). A triggered alert is switched off and pushed to every open page, which shows a browser
notification (if you allowed notifications) and an on-page message.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from app.db.session import Database
from app.models.tables import Alert, Asset
from app.timeutil import utcnow

log = logging.getLogger(__name__)

CONDITIONS = {"above": "Price rises to or above", "below": "Price falls to or below", "pct_move": "Moves at least (±%)"}


def should_trigger(condition: str, value: float, price: float | None, change_pct: float | None) -> bool:
    if price is None:
        return False
    if condition == "above":
        return price >= value
    if condition == "below":
        return price <= value
    if condition == "pct_move":
        return change_pct is not None and abs(change_pct) >= abs(value)
    return False


class AlertService:
    def __init__(self, db: Database, feeds) -> None:  # noqa: ANN001
        self.db = db
        self.feeds = feeds
        self.active: dict[int, list[dict[str, Any]]] = {}
        self.reload()

    def reload(self) -> None:
        active: dict[int, list[dict[str, Any]]] = {}
        with self.db.session() as s:
            for a in s.scalars(select(Alert).where(Alert.active.is_(True))):
                active.setdefault(a.asset_id, []).append({"id": a.id, "condition": a.condition, "value": a.value})
        self.active = active

    def list(self) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.execute(select(Alert, Asset.symbol, Asset.name, Asset.decimals).join(Asset, Asset.id == Alert.asset_id)
                             .order_by(Alert.active.desc(), Alert.created_at.desc())).all()
            return [{"id": a.id, "symbol": sym, "name": name, "decimals": dec, "condition": a.condition,
                     "condition_text": CONDITIONS.get(a.condition, a.condition), "value": a.value, "active": a.active,
                     "created_at": a.created_at.isoformat(), "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None,
                     "triggered_price": a.triggered_price} for a, sym, name, dec in rows]

    def create(self, symbol: str, condition: str, value: float) -> int:
        if condition not in CONDITIONS:
            raise ValueError("Unknown condition")
        with self.db.session() as s:
            asset_id = s.scalar(select(Asset.id).where(Asset.symbol == symbol.upper()))
            if asset_id is None:
                raise ValueError("Unknown asset")
            a = Alert(asset_id=asset_id, condition=condition, value=float(value), active=True, created_at=utcnow())
            s.add(a)
            s.flush()
            new_id = a.id
        self.reload()
        return new_id

    def delete(self, alert_id: int) -> None:
        with self.db.session() as s:
            a = s.get(Alert, alert_id)
            if a:
                s.delete(a)
        self.reload()

    def check(self, asset_id: int) -> None:
        """Called by the price engine after each update (sync, cheap)."""
        alerts = self.active.get(asset_id)
        if not alerts:
            return
        v = self.feeds.view(asset_id)
        if v["status"] not in ("LIVE", "DELAYED") or v.get("kind") == "daily":
            return
        for al in list(alerts):
            if should_trigger(al["condition"], al["value"], v["price"], v["change_pct"]):
                alerts.remove(al)
                self._fire(al, v)

    def _fire(self, al: dict[str, Any], v: dict[str, Any]) -> None:
        with self.db.session() as s:
            a = s.get(Alert, al["id"])
            if a is None or not a.active:
                return
            a.active, a.triggered_at, a.triggered_price = False, utcnow(), v["price"]
        msg = {"type": "alert", "alert": {"id": al["id"], "symbol": v["symbol"], "name": v["name"], "condition": al["condition"],
                                          "condition_text": CONDITIONS[al["condition"]], "value": al["value"], "price": v["price"],
                                          "change_pct": v["change_pct"], "decimals": v["decimals"], "source": v["source"],
                                          "time": v["source_timestamp"]}}
        log.info("Alert %d triggered: %s %s %s (price %s)", al["id"], v["symbol"], al["condition"], al["value"], v["price"])
        try:
            asyncio.get_running_loop().create_task(self.feeds.broadcaster.send_all(msg))
        except RuntimeError:
            pass
