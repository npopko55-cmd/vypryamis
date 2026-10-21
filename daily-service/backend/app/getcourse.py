"""GetCourse Import API checkout and asynchronous Export API reconciliation."""
import base64
import json
from datetime import timedelta
from urllib.parse import urlparse
import httpx
import redis
from .config import settings
from .models import now


class ProviderError(Exception): pass


def api_request(path, *, method="GET", params=None, data=None):
    cfg = settings()
    if not cfg.gc_account_url or not cfg.gc_api_key: raise ProviderError("GetCourse ещё не подключён")
    if cfg.production:
        client = redis.Redis.from_url(cfg.redis_url)
        bucket = "gc:budget:" + str(int(now().timestamp()) // 7200)
        used = client.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],7200) end; return n", 1, bucket)
        if used > 60: raise ProviderError("Достигнут внутренний лимит API GetCourse")
    try:
        with httpx.Client(timeout=25, follow_redirects=False) as client:
            if method == "POST": response = client.post(cfg.gc_account_url.rstrip("/") + path, data={"key": cfg.gc_api_key, **(data or {})})
            else: response = client.get(cfg.gc_account_url.rstrip("/") + path, params={"key": cfg.gc_api_key, **(params or {})})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError): raise ProviderError("GetCourse временно недоступен") from None
    if not isinstance(payload, dict) or payload.get("success") in {False, "false"}:
        raise ProviderError("GetCourse отклонил запрос. Проверьте настройки интеграции")
    return payload


def create_checkout(order, user):
    offer = settings().gc_base_offer if order.product == "base" else settings().gc_full_offer
    if not offer: raise ProviderError("Предложение GetCourse ещё не настроено")
    params = {"user": {"email": user.email, "first_name": user.name}, "system": {"refresh_if_exists": 0, "return_payment_link": 1, "return_deal_number": 1}, "deal": {"deal_number": "VP-" + order.id, "offer_code": offer, "quantity": 1, "deal_cost": order.amount / 100, "deal_currency": "RUB", "deal_comment": "Выпрямись · " + order.id}}
    encoded = base64.b64encode(json.dumps(params, ensure_ascii=False).encode()).decode()
    payload = api_request("/pl/api/deals", method="POST", data={"action": "add", "params": encoded})
    info = payload.get("result", {})
    if info.get("success") in {False, "false"} or not info.get("deal_id") or not info.get("payment_link"):
        raise ProviderError("GetCourse не вернул ссылку на оплату")
    link = str(info["payment_link"])
    # Redirect only to this merchant's GetCourse domain; never trust arbitrary URL payloads.
    if urlparse(link).scheme != "https" or urlparse(link).hostname != urlparse(settings().gc_account_url).hostname:
        raise ProviderError("Адрес оплаты не соответствует домену GetCourse")
    return str(info["deal_id"]), str(info.get("deal_number", "VP-" + order.id)), link


def start_export():
    today = now().date()
    return api_request("/pl/api/account/deals", params={"status_changed_at[from]": (today - timedelta(days=7)).isoformat(), "status_changed_at[to]": (today + timedelta(days=1)).isoformat()}).get("info", {}).get("export_id")


def poll_export(export_id):
    payload = api_request("/pl/api/account/exports/" + str(export_id))
    info = payload.get("info") or {}
    fields, items = info.get("fields"), info.get("items")
    if not fields or items is None: return None
    return [dict(zip(fields, item)) for item in items]


def normalize_export(row):
    return {"order_id": row.get("ID заказа", row.get("id", "")), "order_number": row.get("Номер", row.get("number", "")), "email": row.get("Email", row.get("email", "")), "status": row.get("Статус", row.get("status", "")), "amount": row.get("Стоимость, RUB", row.get("cost", 0)), "currency": row.get("Валюта", "RUB")}
