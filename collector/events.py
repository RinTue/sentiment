"""Nghiên cứu sự kiện: số liệu vĩ mô lớn của Mỹ tác động lên giá BTC ra sao.

- Lịch công bố lấy từ FRED (cần FRED_API_KEY), lưu vào data/events_calendar.json; Fed theo lịch họp FOMC.
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
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CAL_FILE = os.path.join(DATA, "events_calendar.json")
EV_FILE = os.path.join(DATA, "events.json")
MOVES_FILE = os.path.join(DATA, "big_moves.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
START = datetime(2022, 1, 1, tzinfo=timezone.utc)
MAX_NEW_PER_RUN = 60

# Lịch công bố lấy từ FRED (máy chủ GitHub bị Investing.com chặn). Giờ công bố theo thông lệ: 8:30 sáng giờ New York,
# riêng Fed công bố lãi suất lúc 14:00. Chiều bất ngờ (tốt hay xấu cho USD so với dự báo) do trang web lấy từ Investing.com.
NY = ZoneInfo("America/New_York")
GROUPS = [   # (mã, tên tiếng Việt, mã bản tin trên FRED, từ khóa kiểm tra tên bản tin)
    ("cpi", "CPI (lạm phát)", 10, "consumer price"),
    ("nfp", "Bảng lương phi nông nghiệp (NFP)", 50, "employment situation"),
    ("pce", "PCE (lạm phát Fed theo dõi)", 54, "personal income"),
    ("ppi", "PPI (giá sản xuất)", 46, "producer price"),
    ("retail", "Doanh số bán lẻ", 9, "retail"),
    ("gdp", "GDP", 53, "gross domestic product"),
    ("fed", "Fed công bố lãi suất", None, None),
]
FOMC = ["2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
        "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
        "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def _utc(day: str, hh: int, mm: int) -> str:
    d = datetime.strptime(day, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=NY)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fred_release_dates(rid: int, key: str) -> tuple[str, list]:
    meta = requests.get("https://api.stlouisfed.org/fred/release", params={"release_id": rid, "api_key": key, "file_type": "json"}, timeout=20)
    meta.raise_for_status()
    name = (meta.json().get("releases") or [{}])[0].get("name", "")
    r = requests.get("https://api.stlouisfed.org/fred/release/dates",
                     params={"release_id": rid, "api_key": key, "file_type": "json", "realtime_start": START.strftime("%Y-%m-%d"),
                             "realtime_end": "9999-12-31", "include_release_dates_with_no_data": "true", "limit": 1000}, timeout=20)
    r.raise_for_status()
    return name, [x["date"] for x in r.json().get("release_dates", [])]


def schedule(errs: dict) -> list:
    """Danh sách các lần công bố (quá khứ và sắp tới), lưu lại để lần sau không phải hỏi FRED nếu FRED lỗi."""
    cal = _load(CAL_FILE, {"items": [], "saved": 0})
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key and time.time() - cal.get("saved", 0) > 6 * 3600:
        items = []
        for code, name, rid, kw in GROUPS:
            if rid is None:
                items += [{"g": code, "name": name, "t": _utc(d, 14, 0)} for d in FOMC]
                continue
            try:
                rname, dates = fred_release_dates(rid, key)
                if kw not in rname.lower():
                    errs["fred_" + code] = f"mã {rid} là '{rname}'"
                    continue
                items += [{"g": code, "name": name, "t": _utc(d, 8, 30)} for d in dates if d >= START.strftime("%Y-%m-%d")]
            except Exception as ex:  # noqa: BLE001
                errs["fred_" + code] = str(ex)[:100]
            time.sleep(0.3)
        if items:
            cal = {"items": items, "saved": time.time()}
            _save(CAL_FILE, cal)
    elif not cal["items"]:
        cal["items"] = [{"g": "fed", "name": "Fed công bố lãi suất", "t": _utc(d, 14, 0)} for d in FOMC]
    seen, out = set(), []
    for x in cal["items"]:
        k = f"{x['g']}|{x['t']}"
        if k not in seen:
            seen.add(k)
            out.append(dict(x, key=k))
    return sorted(out, key=lambda x: x["t"])


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
    def med(v):
        v = [x for x in v if x is not None]
        return round(statistics.median(v), 2) if v else None
    return {"n": len(rows), "abs60": med([abs(r["re"]["r60"]) for r in rows if r["re"]["r60"] is not None]),
            "abs24": med([abs(r["re"]["r24"]) for r in rows if r["re"]["r24"] is not None]),
            "range60": med([r["re"]["range60"] for r in rows]), "x_normal": med([r["re"]["x_normal"] for r in rows])}


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
        by_day.setdefault(d, []).append(r["name"])
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
    rels = schedule(errs)
    done = _load(EV_FILE, {"items": {}})
    items = done["items"]
    now = time.time()
    ts_of = lambda r: datetime.fromisoformat(r["t"].replace("Z", "+00:00")).timestamp()  # noqa: E731
    todo = sorted([r for r in rels if r["key"] not in items and ts_of(r) < now - 25 * 3600], key=lambda r: r["t"], reverse=True)
    n_new = 0
    for r in todo[:MAX_NEW_PER_RUN]:
        try:
            re_ = reaction(int(ts_of(r)))
        except Exception as ex:  # noqa: BLE001
            errs["coinbase"] = str(ex)[:120]
            break
        items[r["key"]] = {"key": r["key"], "g": r["g"], "name": r["name"], "t": r["t"], "re": re_}
        n_new += 1
        time.sleep(0.25)
    _save(EV_FILE, done)
    try:
        log_big_move(latest, store)
    except Exception as ex:  # noqa: BLE001
        errs["big_move"] = str(ex)[:120]
    ok = [x for x in items.values() if x.get("re")]
    groups = []
    for code, name, _, _ in GROUPS:
        rows = [x for x in ok if x["g"] == code]
        if rows:
            groups.append({"g": code, "name": name, **stats(rows)})
    gmap = {g["g"]: g for g in groups}
    upcoming = [{"t": r["t"], "g": r["g"], "name": r["name"], "abs60": (gmap.get(r["g"]) or {}).get("abs60"), "n": (gmap.get(r["g"]) or {}).get("n")}
                for r in rels if now < ts_of(r) <= now + 14 * 86400]
    # Toàn bộ phản ứng (gọn) để trang ghép với kết quả thực tế/dự báo của Investing.com và tính thống kê theo chiều bất ngờ.
    all_re = [[x["key"], x["re"]["r15"], x["re"]["r60"], x["re"]["r24"], x["re"]["x_normal"]] for x in sorted(ok, key=lambda x: x["t"])]
    pending = len([r for r in rels if r["key"] not in items and ts_of(r) < now - 25 * 3600])
    out = {"groups": groups, "upcoming": upcoming[:12], "all": all_re, "big_days": big_days(rels, _load(MOVES_FILE, {"items": []}))[:40],
           "n_events": len(ok), "pending": pending, "since": min((x["t"] for x in ok), default=None), "errors": errs, "runtime_sec": round(time.time() - t0, 1)}
    print(f"Sự kiện: {len(ok)} lần công bố đã đo, thêm {n_new}, còn {pending}, lỗi: {list(errs)}, {out['runtime_sec']} giây")
    return out
