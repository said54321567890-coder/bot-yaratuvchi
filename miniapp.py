"""Telegram Mini App: bot boshqaruv paneli (botlarim ro'yxati va sozlamalar).

start.py shu modulning handle() funksiyasini chaqiradi. Modulda xato bo'lsa ham
asosiy bot ishlashda davom etadi.
"""
import os, re, json, hmac, hashlib, time
from urllib.parse import parse_qsl

HERE = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.environ.get("MINIAPP_DB", "bot_yaratuvchi")
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
_db = None


def _get_db():
    global _db
    if _db is None:
        uri = None
        for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL", "MONGODB_URL", "DATABASE_URL"):
            v = os.environ.get(name, "")
            if v.startswith(("mongodb://", "mongodb+srv://")):
                uri = v
                break
        if uri is None:
            for v in os.environ.values():
                if v.startswith(("mongodb://", "mongodb+srv://")):
                    uri = v
                    break
        if uri is None:
            return None
        from pymongo import MongoClient
        _db = MongoClient(uri, serverSelectionTimeoutMS=5000)[DB_NAME]
    return _db


def _tokens():
    out = []
    for v in os.environ.values():
        v = v.strip()
        if TOKEN_RE.match(v):
            out.append(v)
    return out


def _verify(init_data):
    """Telegram initData imzosini tekshiradi. To'g'ri bo'lsa user dict qaytaradi."""
    try:
        if not init_data:
            return None
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        if not got:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        for tok in _tokens():
            secret = hmac.new(b"WebAppData", tok.encode(), hashlib.sha256).digest()
            calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            if hmac.compare_digest(calc, got):
                if time.time() - int(pairs.get("auth_date", 0)) > 86400:
                    return None
                return json.loads(pairs["user"])
    except Exception:
        return None
    return None


def _json(status, obj):
    return status, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode()


def _bot_view(db, b):
    ids = [str(b["_id"])]
    if b.get("tg_id") is not None:
        ids.append(str(b["tg_id"]))
    try:
        users = db.bot_users.count_documents({"bot_id": {"$in": ids}})
    except Exception:
        users = 0
    return {
        "id": str(b["_id"]),
        "title": b.get("title") or "",
        "username": b.get("username") or "",
        "plan": b.get("plan") or "",
        "engine": b.get("engine") or "",
        "template": b.get("template") or "",
        "active": bool(b.get("active", False)),
        "expires": b.get("expires"),
        "created": b.get("created"),
        "limit": b.get("limit"),
        "speed": b.get("speed"),
        "welcome": b.get("welcome") or "",
        "users": users,
    }


def handle(method, path, headers, body):
    """(status, content_type, bytes) qaytaradi yoki None (boshqa yo'llar uchun)."""
    path = path.split("?")[0]

    if method == "GET" and path in ("/app", "/app/"):
        with open(os.path.join(HERE, "miniapp.html"), "rb") as f:
            return 200, "text/html; charset=utf-8", f.read()

    if not path.startswith("/api/"):
        return None

    user = _verify(headers.get("X-Init-Data", ""))
    if not user:
        return _json(401, {"error": "auth"})
    db = _get_db()
    if db is None:
        return _json(500, {"error": "db"})
    uid = int(user["id"])

    try:
        if method == "GET" and path == "/api/me":
            acc = db.accounts.find_one({"user_id": uid, "scope": "root"}) or db.accounts.find_one({"user_id": uid})
            bots = [_bot_view(db, b) for b in db.bots.find({"owner_id": uid}).sort("created", -1).limit(100)]
            return _json(200, {
                "user": {"id": uid, "name": user.get("first_name", "")},
                "balance": (acc or {}).get("balance", 0),
                "bots": bots,
            })

        if method == "POST" and path == "/api/bot":
            from bson import ObjectId
            data = json.loads(body or b"{}")
            oid = ObjectId(str(data.get("id", "")))
            upd = {}
            if "active" in data:
                upd["active"] = bool(data["active"])
            if "welcome" in data:
                w = str(data["welcome"]).strip()
                if not w or len(w) > 1000:
                    return _json(400, {"error": "welcome"})
                upd["welcome"] = w
            if not upd:
                return _json(400, {"error": "empty"})
            r = db.bots.update_one({"_id": oid, "owner_id": uid}, {"$set": upd})
            if r.matched_count == 0:
                return _json(404, {"error": "not_found"})
            return _json(200, {"ok": True})
    except Exception:
        return _json(500, {"error": "server"})

    return _json(404, {"error": "not_found"})
