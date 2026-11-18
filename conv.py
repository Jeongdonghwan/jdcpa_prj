"""전환 수집(스펙 7.4): 포스트백·CSV → conversions upsert(dedup), 일 롤업, EPC·캡 스냅샷."""
import csv
import io
import json

from adapters.base import NetworkAdapter
from db import js, ops, queries as dbq


def record_conversion(network, ev, source):
    """verify·parse 이후 단계. 'orphan' | 'created' | 'updated' | 'duplicate' 를 돌려준다."""
    click = ev["click_id"] and ops.click(ev["click_id"])
    if not click:
        ops.insert_orphan(network["id"], ev["raw"], "click_id 없음" if not ev["click_id"] else "click_id 매칭 실패")
        return "orphan"
    external_id = ev["external_id"] or f"{ev['click_id']}:{ev['event']}"  # dedup 키: network_id + external_id
    existing = ops.conversion(network["id"], external_id)
    if existing:
        if existing["status"] == ev["status"] and (ev["amount"] is None or float(existing["amount"] or 0) == ev["amount"]):
            return "duplicate"
        ops.update_conversion(existing["id"], ev["status"], ev["amount"])
        # 상태 이력: 10.1 DDL에 전용 이력 테이블이 없어 audit_log 에 남긴다
        dbq.audit(source, "conversion_status", f"conversions:{existing['id']}",
                  json.dumps({"from": existing["status"], "to": ev["status"], "amount": ev["amount"]}))
        return "updated"
    ops.insert_conversion(network_id=network["id"], offer_id=click["offer_id"], click_id=click["click_id"], external_id=external_id,
                          event=ev["event"], status=ev["status"], amount=ev["amount"], source=source, match_quality="exact",
                          raw=json.dumps(ev["raw"], ensure_ascii=False, default=str), occurred_at=ev["occurred_at"])
    return "created"


def import_csv(network, text):
    """포스트백이 없는 네트워크용. networks.csv_mapping = {click_id:'열', external_id:'열', status:'열', amount:'열', occurred_at:'열', status_map:{}}"""
    m = js(network["csv_mapping"], {})
    if not m.get("click_id"):
        raise ValueError("networks.csv_mapping 에 click_id 열 이름이 없습니다")
    counts = {}
    for row in csv.DictReader(io.StringIO(text)):
        # ponytail: click_id 없는 행의 (오퍼, 시각±N분, 지역) 근사 매칭은 미구현 — orphan 으로 쌓이니 필요해지면 추가
        ev = NetworkAdapter.event_from(
            click_id=row.get(m["click_id"]), external_id=row.get(m.get("external_id", "")), status=row.get(m.get("status", "")),
            amount=row.get(m.get("amount", "")), event=row.get(m.get("event", "")), raw=row, status_map=m.get("status_map"),
            occurred_at=row.get(m.get("occurred_at", "")))
        result = record_conversion(network, ev, "csv")
        counts[result] = counts.get(result, 0) + 1
    return counts


def rerank(site):
    """EPC·반려율·캡 도달 여부를 스냅샷으로 저장하고 ranking_version 을 올린다(하루 1회 이상 cron)."""
    offers, capped = {}, []
    for r in ops.offer_stats_7d():
        clicks = int(r["clicks"])
        offers[str(r["id"])] = {"clicks": clicks, "epc": round(float(r["revenue"]) / clicks, 2) if clicks else 0,
                                "reject_rate": round(int(r["rejected"]) / int(r["convs"]), 3) if r["convs"] else 0}
        if (r["daily_cap"] and r["today"] >= r["daily_cap"]) or (r["monthly_cap"] and r["month"] >= r["monthly_cap"]):
            capped.append(r["id"])
    ops.set_setting(site["id"], "offer_snapshot", json.dumps({"offers": offers, "capped": capped}))
    ops.bump_ranking_version(site["id"])
    return offers, capped
