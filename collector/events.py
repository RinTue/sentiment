"""Nghiên cứu sự kiện: số liệu vĩ mô lớn của Mỹ tác động lên giá BTC ra sao.

- Lịch sử sự kiện (giờ công bố, thực tế, dự báo) lấy từ Investing.com, lưu dần vào data/events_calendar.json.
- Với mỗi lần công bố: giá BTC (Coinbase, nến 15 phút) trước và sau 15 phút, 1 giờ, 24 giờ; so với dao động bình thường
  trong 24 giờ trước đó. Kết quả lưu vào data/events.json để không phải tính lại.
- Nhật ký biến động lớn: ngày BTC đi từ 5% trở lên, kèm sự kiện vĩ mô trong ngày và (từ khi hệ thống chạy) tin nổi bật, nguyên nhân đà giá.
"""
from __future__ import annotations

import csv
import json
import os
import re
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CAL_FILE = os.path.join(DATA, "events_calendar.json")
EV_FILE = os.path.join(DATA, "events.json")
MOVES_FILE = os.path.join(DATA, "big_moves.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
START = datetime(2022, 1, 1, tzinfo=timezone.utc)
MAX_NEW_PER_RUN = 60

# Nhóm sự kiện: (mã, tên tiếng Việt, mẫu tên tiếng Anh, độ ưu tiên khi nhiều tin cùng giờ — số nhỏ là tin chính)
GROUPS = [
    ("cpi", "CPI (lạm phát)", r"^Core CPI \(MoM\)|^CPI \(MoM\)|^CPI \(YoY\)|^Core CPI \(YoY\)", 1),
    ("nfp", "Bảng lương phi nông nghiệp (NFP)", r"^Nonfarm Payrolls|^Unemployment Rate", 1),
    ("fed", "Fed công bố lãi suất", r"^Fed Interest Rate Decision", 1),
    ("pce", "PCE (lạm phát Fed theo dõi)", r"^Core PCE Price Index \(MoM\)|^PCE Price index \(MoM\)", 2),
    ("ppi", "PPI (giá sản xuất)", r"^PPI \(MoM\)|^Core PPI \(MoM\)", 2),
    ("retail", "Doanh số bán lẻ", r"^Retail Sales \(MoM\)|^Core Retail Sales \(MoM\)", 2),
    ("gdp", "GDP", r"^GDP \(QoQ\)", 2),
]
PRIMARY = {"cpi": r"^Core CPI \(MoM\)", "nfp": r"^Nonfarm Payrolls", "fed": r"^Fed Interest Rate", "pce": r"^Core PCE", "ppi": r"^PPI \(MoM\)",
           "retail": r"^Retail Sales \(MoM\)", "gdp": r"^GDP \(QoQ\)"}


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def investing(start: datetime, end: datetime) -> list:
    """Các lần công bố mức quan trọng cao của Mỹ trong khoảng thời gian, kèm tên sự kiện."""
    base = "https://endpoints.investing.com/pd-instruments/v1/calendars/economic/events/occurrences"
    out, cursor = [], None
    for _ in range(10):
        params = {"domain_id": 1, "limit": 200, "country_ids": 5, "importance": "high",
                  "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(base, params=params, headers={"User-Agent": UA, "Accept": "application/json", "Origin": "https://www.investing.com",
                                                       "Referer": "https://www.investing.com/"}, timeout=20)
        r.raise_for_status()
        j = r.json()
        names = {e["event_id"]: (e.get("event_translated") or e.get("short_name") or "") for e in j.get("events", [])}
        for o in j.get("occurrences", []):
            out.append({"id": o.get("occurrence_id"), "t": o["occurrence_time"], "name": names.get(o["event_id"], ""), "actual": o.get("actual"),
                        "forecast": o.get("forecast"), "previous": o.get("previous"), "atf": o.get("actual_to_forecast")})
        cursor = j.get("next_page_cursor")
        if not cursor or not j.get("occurrences"):
            break
        time.sleep(0.5)
    return out


def update_calendar(errs: dict) -> list:
    """Lần đầu tải dần từ 2022 theo từng quý (tối đa 8 quý mỗi lần chạy); lần nào cũng tải lại 45 ngày qua và 30 ngày tới."""
    cal = _load(CAL_FILE, {"items": {}, "backfilled_to": None})
    items = cal["items"]
    now = datetime.now(timezone.utc)
    edge = now - timedelta(days=45)
    bf = datetime.fromisoformat(cal["backfilled_to"]) if cal.get("backfilled_to") else START
    for _ in range(8):
        if bf >= edge:
            break
        b = min(bf + timedelta(days=92), edge)
        try:
            for o in investing(bf, b):
                items[str(o["id"])] = o
        except Exception as ex:  # noqa: BLE001
            errs["calendar"] = str(ex)[:120]
            break
        bf = b
        cal["backfilled_to"] = bf.isoformat()
        time.sleep(0.5)
    try:
        for o in investing(edge, now + timedelta(days=30)):
            items[str(o["id"])] = o
    except Exception as ex:  # noqa: BLE001
        errs["calendar"] = str(ex)[:120]
    _save(CAL_FILE, cal)
    return list(items.values())


def releases(cal: list) -> list:
    """Gộp các tin cùng nhóm, cùng giờ thành một lần công bố; lấy chiều bất ngờ theo tin chính."""
    by_key: dict = {}
    for o in cal:
        g = next((g for g in GROUPS if re.search(g[2], o["name"] or "")), None)
        if not g:
            continue
        key = f"{g[0]}|{o['t']}"
        by_key.setdefault(key, {"g": g[0], "name": g[1], "t": o["t"], "items": []})["items"].append(o)
    out = []
    for r in by_key.values():
        prim = next((o for o in r["items"] if re.search(PRIMARY[r["g"]], o["name"])), r["items"][0])
        r["atf"] = prim.get("atf") if prim.get("actual") is not None else None
        r["detail"] = [{"name": o["name"], "actual": o.get("actual"), "forecast": o.get("forecast"), "previous": o.get("previous")} for o in r["items"]]
        r["key"] = f"{r['g']}|{r['t']}"
        out.append(r)
    return sorted(out, key=lambda r: r["t"])


def coinbase_15m(t0: int, t1: int) -> list:
    r = requests.get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                     params={"granularity": 900, "start": datetime.fromtimestamp(t0, timezone.utc).isoformat(), "end": datetime.fromtimestamp(t1, timezone.utc).isoformat()},
                     headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return sorted((int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])) for c in r.json())   # (t, low, high, open, close)


def reaction(t: int) -> dict | None:
    """Giá BTC quanh giờ công bố t (giây). Trả về % thay đổi và mức so với dao động bình thường 24 giờ trước."""
    c = coinbase_15m(t - 24 * 3600, t + 24 * 3600)
    if len(c) < 150:
        return None
    by = {x[0]: x for x in c}
    pre = [x for x in c if x[0] < t]
    if not pre or t not in by:
        return None
    p0 = pre[-1][4]
    def close_at(dt):
        k = t + dt - 900
        x = by.get(k)
        return x[4] if x else None
    r15, r60, r24 = close_at(900), close_at(3600), close_at(86400)
    first_hour = [x for x in c if t <= x[0] < t + 3600]
    hi, lo = max([x[2] for x in first_hour] + [p0]), min([x[1] for x in first_hour] + [p0])
    hourly = [x[4] for x in pre if (x[0] - t) % 3600 == 0]
    moves = [abs(hourly[i] / hourly[i - 1] - 1) * 100 for i in range(1, len(hourly))]
    normal = statistics.median(moves) if len(moves) >= 10 else None
    pct = lambda v: None if v is None else round((v / p0 - 1) * 100, 2)  # noqa: E731
    out = {"p0": round(p0), "r15": pct(r15), "r60": pct(r60), "r24": pct(r24), "range60": round((hi - lo) / p0 * 100, 2),
           "normal60": None if normal is None else round(normal, 2)}
    out["x_normal"] = round(abs(out["r60"]) / normal, 1) if normal and out["r60"] is not None else None
    return out


def stats(rows: list) -> dict:
    def avg(v):
        v = [x for x in v if x is not None]
        return round(sum(v) / len(v), 2) if v else None
    def med(v):
        v = [x for x in v if x is not None]
        return round(statistics.median(v), 2) if v else None
    out = {"n": len(rows), "abs60": med([abs(r["re"]["r60"]) for r in rows if r["re"]["r60"] is not None]),
           "range60": med([r["re"]["range60"] for r in rows]), "x_normal": med([r["re"]["x_normal"] for r in rows])}
    for side, lab in (("positive", "usd_up"), ("negative", "usd_dn")):
        sel = [r for r in rows if r.get("atf") == side and r["re"]["r60"] is not None]
        if not sel:
            out[lab] = {"n": 0}
            continue
        # "positive" = tốt cho USD, thường bất lợi cho crypto: đếm số lần BTC giảm; "negative" thì đếm số lần tăng.
        hit60 = sum(1 for r in sel if (r["re"]["r60"] < 0) == (side == "positive"))
        hit24 = sum(1 for r in sel if r["re"]["r24"] is not None and (r["re"]["r24"] < 0) == (side == "positive"))
        n24 = sum(1 for r in sel if r["re"]["r24"] is not None)
        out[lab] = {"n": len(sel), "avg60": avg([r["re"]["r60"] for r in sel]), "avg24": avg([r["re"]["r24"] for r in sel]),
                    "hit60": round(100 * hit60 / len(sel)), "hit24": round(100 * hit24 / n24) if n24 else None}
    return out


def log_big_move(latest: dict, store: dict | None) -> None:
    """Từ nay về sau: khi BTC chạy từ 5% trong 24 giờ, ghi lại tin nổi bật và nguyên nhân đà giá (một lần mỗi ngày UTC)."""
    M = latest.get("market") or {}
    w = ((M.get("drivers") or {}).get("windows") or {}).get("24")
    if not w or abs(w.get("dp", 0)) < 5:
        return
    log = _load(MOVES_FILE, {"items": []})
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if any(x["day"] == day for x in log["items"]):
        return
    head = None
    if store:
        recent = [x for x in store.values() if time.time() - x["t"] <= 86400 and x.get("s") is not None]
        news = [x for x in recent if x.get("kind") == "news"] or recent
        news.sort(key=lambda x: -abs(x["s"]) * (1 if (x["s"] > 0) == (w["dp"] > 0) else 0.3))
        if news:
            head = {"text": news[0].get("title") or news[0]["text"][:200], "src": news[0].get("outlet") or news[0]["src"], "url": news[0].get("url")}
    log["items"].append({"day": day, "t": int(time.time() * 1000), "dp": w["dp"], "doi": w.get("doi"), "label": w.get("label"), "headline": head,
                         "composite": (M.get("composite") or {}).get("value")})
    _save(MOVES_FILE, log)


def big_days(rels: list, store_log: dict) -> list:
    """Các ngày BTC đi từ 5% (giá đóng cửa ngày UTC, Coinbase) trong 365 ngày qua, kèm số liệu vĩ mô công bố hôm đó."""
    path = os.path.join(DATA, "btc_daily.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        rows = [(int(r[0]), float(r[1])) for r in list(csv.reader(f))[1:]]
    rows.sort()
    by_day = {}
    for r in rels:
        d = r["t"][:10]
        by_day.setdefault(d, []).append(r["name"] + (" (tốt cho USD)" if r.get("atf") == "positive" else " (xấu cho USD)" if r.get("atf") == "negative" else ""))
    logged = {x["day"]: x for x in store_log.get("items", [])}
    out = []
    cutoff = time.time() - 365 * 86400
    for i in range(1, len(rows)):
        t, c = rows[i]
        if t < cutoff:
            continue
        ch = (c / rows[i - 1][1] - 1) * 100
        if abs(ch) >= 5:
            d = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
            out.append({"day": d, "ch": round(ch, 1), "close": round(c), "macro": by_day.get(d, []), "log": logged.get(d)})
    for d, x in logged.items():   # ngày đã ghi theo cửa sổ 24 giờ nhưng chưa có trong giá đóng cửa ngày
        if not any(o["day"] == d for o in out):
            out.append({"day": d, "ch": x["dp"], "close": None, "macro": by_day.get(d, []), "log": x})
    return sorted(out, key=lambda x: x["day"], reverse=True)


def run(latest: dict, store: dict | None = None) -> dict:
    t0, errs = time.time(), {}
    cal = update_calendar(errs)
    rels = releases(cal)
    done = _load(EV_FILE, {"items": {}})
    items = done["items"]
    now = time.time()
    todo = [r for r in rels if r["key"] not in items and r.get("atf") is not None
            and datetime.fromisoformat(r["t"].replace("Z", "+00:00")).timestamp() < now - 25 * 3600]
    todo.sort(key=lambda r: r["t"], reverse=True)      # tính các lần gần đây trước, phần cũ bổ sung dần mỗi giờ
    n_new = 0
    for r in todo[:MAX_NEW_PER_RUN]:
        t = int(datetime.fromisoformat(r["t"].replace("Z", "+00:00")).timestamp())
        try:
            re_ = reaction(t)
        except Exception as ex:  # noqa: BLE001
            errs["coinbase"] = str(ex)[:120]
            break
        items[r["key"]] = {"g": r["g"], "name": r["name"], "t": r["t"], "atf": r["atf"], "detail": r["detail"], "re": re_}
        n_new += 1
        time.sleep(0.25)
    _save(EV_FILE, done)
    try:
        log_big_move(latest, store)
    except Exception as ex:  # noqa: BLE001
        errs["big_move"] = str(ex)[:120]
    ok = [x for x in items.values() if x.get("re")]
    by_g = {}
    for x in ok:
        by_g.setdefault(x["g"], []).append(x)
    groups = []
    for code, name, _, _ in GROUPS:
        if code in by_g:
            groups.append({"g": code, "name": name, **stats(by_g[code])})
    upcoming = []
    for r in rels:
        ts = datetime.fromisoformat(r["t"].replace("Z", "+00:00")).timestamp()
        if now < ts <= now + 14 * 86400:
            g = next((x for x in groups if x["g"] == r["g"]), None)
            upcoming.append({"t": r["t"], "g": r["g"], "name": r["name"], "detail": r["detail"],
                             "abs60": g and g["abs60"], "range60": g and g["range60"], "n": g and g["n"]})
    recent = sorted(ok, key=lambda x: x["t"], reverse=True)[:30]
    pending = len([r for r in rels if r["key"] not in items and r.get("atf") is not None])
    out = {"groups": groups, "upcoming": upcoming[:12], "recent": recent, "big_days": big_days(rels, _load(MOVES_FILE, {"items": []}))[:40],
           "n_events": len(ok), "pending": pending, "since": min((x["t"] for x in ok), default=None), "errors": errs,
           "runtime_sec": round(time.time() - t0, 1)}
    print(f"Sự kiện: {len(ok)} lần công bố đã đo, thêm {n_new}, còn {pending}, lỗi: {list(errs)}, {out['runtime_sec']} giây")
    return out
