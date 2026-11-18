"""네트워크 어댑터(스펙 7.5). 설정만으로 되는 부분은 GenericAdapter, 비표준 서명·API 폴링만 서브클래스로."""
import hmac
import importlib
import json
import smtplib
import urllib.request
from email.message import EmailMessage
from urllib.parse import quote

from db import js


class NetworkAdapter:
    slug = ""

    def __init__(self, network):
        self.network = network
        self.conventions = js(network["conventions"], {})
        self.postback = js(network["postback"], {})

    def build_tracking_url(self, offer, click, ctx, template=None):
        """템플릿의 {매크로}를 치환한다. 값은 max_sub_len 으로 자르고 URL 인코딩."""
        max_len = int(self.conventions.get("max_sub_len", 64))
        url = template or offer["tracking_url_template"] or ""
        for key, value in {**ctx, "click_id": click["click_id"]}.items():
            url = url.replace("{%s}" % key, quote(str(value or "")[:max_len], safe=""))
        return url

    def verify_postback(self, request):
        """시크릿 파라미터 == 설정값 AND IP allowlist. 둘 다 설정이 없으면 거부한다."""
        cfg = self.postback
        if not cfg.get("enabled") or not (cfg.get("secret") or cfg.get("ip_allowlist")):
            return False
        if cfg.get("ip_allowlist") and request.remote_addr not in cfg["ip_allowlist"]:
            return False
        if cfg.get("secret"):
            given = request.values.get(cfg.get("secret_param", "secret"), "")
            return hmac.compare_digest(given.encode(), str(cfg["secret"]).encode())
        return True

    def parse_postback(self, request):
        """설정의 파라미터 매핑으로 전환 이벤트 dict 추출."""
        cfg, v = self.postback, request.values
        return self.event_from(
            click_id=v.get(cfg.get("click_id_param", "click_id")), external_id=v.get(cfg.get("external_id_param", "txid")),
            status=v.get(cfg.get("status_param", "status")), amount=v.get(cfg.get("amount_param", "amount")),
            event=v.get(cfg.get("event_param", "event")), raw=v.to_dict(), status_map=cfg.get("status_map"))

    @staticmethod
    def event_from(click_id, external_id, status, amount, event, raw, status_map=None, occurred_at=None):
        incoming = {str(theirs): ours for ours, theirs in (status_map or {}).items()}  # {approved:'1'} → {'1':'approved'}
        status = incoming.get(str(status), status if status in ("pending", "approved", "rejected") else "pending")
        try:
            amount = float(amount) if amount not in (None, "") else None
        except ValueError:
            amount = None
        return {"click_id": (click_id or "").strip() or None, "external_id": (external_id or "").strip() or None, "status": status,
                "amount": amount, "event": (event or "lead")[:30], "raw": raw, "occurred_at": occurred_at or None}

    def pull_conversions(self, since):
        raise NotImplementedError  # 리포트 API가 있는 네트워크만 서브클래스에서 구현

    def forward_lead(self, offer, lead):
        """own_form 리드 전달. forward_config = {method:'webhook'|'sheet'|'email', url|to, headers, field_map}"""
        cfg = js(offer["forward_config"], {})
        payload = {cfg.get("field_map", {}).get(k, k): v for k, v in lead.items()}
        if cfg.get("method") in ("webhook", "sheet", "api"):  # sheet 는 Apps Script 웹앱 URL로 보내는 webhook
            req = urllib.request.Request(cfg["url"], data=json.dumps(payload, ensure_ascii=False).encode(),
                                         headers={"Content-Type": "application/json", **cfg.get("headers", {})})
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read(2000).decode("utf-8", "replace")
                return {"ok": 200 <= r.status < 300, "status": r.status, "body": body}
        if cfg.get("method") == "email":
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = f"[상담 신청] {offer['advertiser']}", cfg.get("from", "noreply@localhost"), cfg["to"]
            msg.set_content("\n".join(f"{k}: {v}" for k, v in payload.items()))
            with smtplib.SMTP(cfg.get("smtp_host", "localhost"), int(cfg.get("smtp_port", 25)), timeout=10) as s:
                s.send_message(msg)
            return {"ok": True, "status": "sent"}
        return {"ok": False, "status": "forward_config.method 미설정"}


class GenericAdapter(NetworkAdapter):
    """설정 기반 기본 구현. 대부분의 네트워크는 이것으로 끝."""


def get_adapter(network):
    """networks.adapter_class 가 비어 있으면 GenericAdapter, 있으면 'adapters.모듈.클래스' 를 import."""
    path = network["adapter_class"]
    if not path:
        return GenericAdapter(network)
    module, _, cls = path.rpartition(".")
    if not module.startswith("adapters."):
        raise ValueError("adapter_class 는 adapters. 아래여야 합니다")
    return getattr(importlib.import_module(module), cls)(network)
