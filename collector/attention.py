"""Mức chú ý của người mới (nhỏ lẻ): tìm kiếm Google, thứ hạng ứng dụng sàn, lượt xem Wikipedia.

- Google Trends: không có API miễn phí chính thức; dùng đường dữ liệu mà chính trang trends.google.com gọi.
  Máy chủ đám mây hay bị Google chặn (HTTP 429), nên chỉ gọi 6 giờ một lần và giữ bản cũ khi lỗi.
- Thứ hạng ứng dụng: bảng xếp hạng App Store công khai của Apple (Mỹ và Việt Nam), ghi lại mỗi giờ vì không có lịch sử.
- Wikipedia: trang Dogecoin và Meme coin so với trang Bitcoin (memecoin được tra nhiều hơn = hưng phấn),
  và trang Bitcoin tiếng Việt.
"""
from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "attention_cache.json")
APPS_FILE = os.path.join(DATA, "app_ranks.csv")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
WIKI_UA = "sentiment-dashboard (github.com/RinTue/sentiment)"

TRENDS = {   # tên nhóm: (vùng, từ khóa) — các từ trong một nhóm được so trên cùng một thang 0–100
    "global": ("", ["buy bitcoin", "buy crypto"]),    # không để chung với "bitcoin": từ đó lớn quá, ép hai từ kia về 0
    "vn": ("VN", ["mua bitcoin", "bitcoin", "tiền ảo", "binance"]),
}
APPS = ["Coinbase", "Binance", "Crypto.com", "OKX", "Bybit"]
COUNTRIES = {"us": "Mỹ", "vn": "Việt Nam"}
WIKI_PAGES = [("en", "Bitcoin"), ("en", "Dogecoin"), ("en", "Meme_coin"), ("en", "Cryptocurrency"), ("vi", "Bitcoin")]


def _load() -> dict:
    try:
        return json.load(open(CACHE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------- Google Trends
def _gjson(text: str):
    return json.loads(text[text.find("{"):])


def trends_fetch(geo: str, kws: list, timeframe: str) -> list:
    """Trả về [[ms, v1, v2, ...], ...] theo thứ tự từ khóa."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    s.get("https://trends.google.com/trends/?geo=" + (geo or "US"), timeout=20)
    req = {"comparisonItem": [{"keyword": k, "geo": geo, "time": timeframe} for k in kws], "category": 0, "property": ""}
    r = s.get("https://trends.google.com/trends/api/explore", params={"hl": "en-US", "tz": "-420", "req": json.dumps(req)}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"explore HTTP {r.status_code}")
    w = next((w for w in _gjson(r.text)["widgets"] if w.get("id") == "TIMESERIES"), None)
    if not w:
        raise RuntimeError("không có widget TIMESERIES")
    time.sleep(2)
    r = s.get("https://trends.google.com/trends/api/widgetdata/multiline",
              params={"hl": "en-US", "tz": "-420", "req": json.dumps(w["request"]), "token": w["token"]}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"multiline HTTP {r.status_code}")
    rows = _gjson(r.text)["default"]["timelineData"]
    out = [[int(x["time"]) * 1000] + [int(v) for v in x["value"]] for x in rows]
    if len(out) < 10:
        raise RuntimeError(f"chỉ có {len(out)} điểm")
    return out


def trends(cache: dict, errs: dict) -> dict:
    T = cache.setdefault("trends", {})
    same = all((T.get("groups") or {}).get(n, {}).get("kw") == kws for n, (_, kws) in TRENDS.items())
    if time.time() - T.get("tried", 0) < 6 * 3600 and T.get("groups") and same:
        return T
    T["tried"] = time.time()
    groups = T.setdefault("groups", {})
    for name, (geo, kws) in TRENDS.items():
        g = groups.setdefault(name, {"geo": geo, "kw": kws})
        if g.get("kw") != kws:          # đổi từ khóa thì bỏ số cũ (thang 0–100 khác nhau)
            g.clear()
            g.update({"geo": geo, "kw": kws})
        for key, tf in (("d90", "today 3-m"), ("y5", "today 5-y")):
            try:
                g[key] = trends_fetch(geo, kws, tf)
                g["kw"] = kws
                g[key + "_at"] = time.time()
                errs.pop(f"trends_{name}_{key}", None)
            except Exception as ex:  # noqa: BLE001
                errs[f"trends_{name}_{key}"] = str(ex)[:120]
            time.sleep(3)
    return T


# ---------------------------------------------------------------- thứ hạng App Store
def _itunes(cc: str, genre: str = "") -> list:
    url = f"https://itunes.apple.com/{cc}/rss/topfreeapplications/limit=200/{'genre=' + genre + '/' if genre else ''}json"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return [e["im:name"]["label"] for e in r.json()["feed"].get("entry", [])]


def _marketing(cc: str) -> list:
    r = requests.get(f"https://rss.marketingtools.apple.com/api/v2/{cc}/apps/top-free/100/apps.json", headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return [a["name"] for a in r.json()["feed"]["results"]]


def _rank(names: list, app: str):
    a = app.lower()
    return next((i + 1 for i, n in enumerate(names) if n.lower().startswith(a)), None)


def app_ranks(errs: dict) -> dict | None:
    rows, src = [], None
    for cc in COUNTRIES:
        overall = finance = None
        try:
            overall = _itunes(cc)
            src = "App Store (iTunes RSS)"
        except Exception as ex:  # noqa: BLE001
            errs[f"apps_{cc}"] = str(ex)[:100]
            try:
                overall = _marketing(cc)
                src = "App Store (Apple Marketing Tools)"
            except Exception as ex2:  # noqa: BLE001
                errs[f"apps2_{cc}"] = str(ex2)[:100]
        try:
            finance = _itunes(cc, "6015")
        except Exception as ex:  # noqa: BLE001
            errs[f"apps_fin_{cc}"] = str(ex)[:100]
        if overall is None and finance is None:
            continue
        for app in APPS:
            rows.append({"cc": cc, "app": app, "overall": _rank(overall or [], app), "finance": _rank(finance or [], app),
                         "of": len(overall or []), "fof": len(finance or [])})
    if not rows:
        return None
    now = datetime.now(timezone.utc)
    new = not os.path.exists(APPS_FILE)
    with open(APPS_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_utc", "country", "app", "overall", "finance"])
        for r in rows:
            w.writerow([now.strftime("%Y-%m-%dT%H:%M:%SZ"), r["cc"], r["app"], r["overall"] or "", r["finance"] or ""])
    # so với khoảng 24 giờ và 7 ngày trước (lịch sử tự ghi)
    hist: dict = {}
    with open(APPS_FILE, encoding="utf-8") as f:
        for h in csv.DictReader(f):
            hist.setdefault((h["country"], h["app"]), []).append((datetime.strptime(h["time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc), h["finance"], h["overall"]))
    for r in rows:
        H = hist.get((r["cc"], r["app"]), [])
        for lab, d in (("d1", 1), ("d7", 7)):
            old = [x for x in H if x[0] <= now - timedelta(days=d)]
            if old:
                r[lab + "_fin"] = int(old[-1][1]) if old[-1][1] else None
                r[lab + "_all"] = int(old[-1][2]) if old[-1][2] else None
        r["since"] = H[0][0].strftime("%Y-%m-%d") if H else None
    return {"src": src, "rows": rows}


# ---------------------------------------------------------------- Wikipedia nhiều trang
def wiki_series(lang: str, page: str, days: int) -> dict:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=days)
    url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/all-access/user/{page}/daily/{start:%Y%m%d}/{end:%Y%m%d}"
    r = requests.get(url, headers={"User-Agent": WIKI_UA}, timeout=25)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return {int(datetime.strptime(i["timestamp"][:8], "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()): i["views"] for i in r.json().get("items", [])}


def wiki(cache: dict, errs: dict) -> dict:
    W = cache.setdefault("wiki", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if W.get("day") != today:
        pages = {}
        for lang, page in WIKI_PAGES:
            try:
                pages[f"{lang}:{page}"] = wiki_series(lang, page, 400)
            except Exception as ex:  # noqa: BLE001
                errs[f"wiki_{lang}_{page}"] = str(ex)[:100]
        if pages.get("en:Bitcoin"):
            W.update({"day": today, "pages": {k: sorted(v.items()) for k, v in pages.items()}})
    P = {k: dict((int(t), v) for t, v in a) for k, a in (W.get("pages") or {}).items()}
    btc = P.get("en:Bitcoin") or {}
    if not btc:
        return {}
    days = sorted(btc)
    meme = {d: (P.get("en:Dogecoin", {}).get(d, 0) + P.get("en:Meme_coin", {}).get(d, 0)) / btc[d] for d in days if btc[d]}
    md = sorted(meme)
    sm = {md[i]: sum(meme[md[k]] for k in range(i - 6, i + 1)) / 7 for i in range(6, len(md))}   # trung bình 7 ngày
    last = sm[max(sm)] if sm else None
    pct = round(100 * sum(1 for v in sm.values() if v <= last) / len(sm)) if sm else None
    cut = days[-1] - 90 * 86400
    out = {"meme_ratio": round(last, 3) if last is not None else None, "meme_pct": pct, "series": {}}
    for k in ("en:Bitcoin", "en:Dogecoin", "en:Meme_coin", "en:Cryptocurrency", "vi:Bitcoin"):
        if P.get(k):
            out["series"][k] = [[d * 1000, P[k][d]] for d in sorted(P[k]) if d >= cut]
    out["meme_series"] = [[d * 1000, round(v, 4)] for d, v in sorted(sm.items()) if d >= cut]
    vi = P.get("vi:Bitcoin") or {}
    if len(vi) >= 40:
        vd = sorted(vi)
        last7 = sum(vi[d] for d in vd[-7:]) / 7
        prior = sum(vi[d] for d in vd[-67:-7]) / len(vd[-67:-7])
        out["vi_ch"] = round((last7 / prior - 1) * 100) if prior else None
        out["vi_last7"] = round(last7)
    return out


# ---------------------------------------------------------------- tổng hợp
def _trend_view(T: dict) -> dict:
    out = {}
    for name, g in (T.get("groups") or {}).items():
        d90 = g.get("d90") or []
        if not d90:
            continue
        kw = g.get("kw") or []
        item = {"geo": g.get("geo"), "kw": kw, "series": d90, "age_h": round((time.time() - g.get("d90_at", 0)) / 3600, 1)}
        # mức hiện tại của từng từ khóa so với 5 năm (tuần gần nhất / đỉnh 5 năm, và phân vị)
        y5 = g.get("y5") or []
        if y5:
            item["y5"] = []
            for i, k in enumerate(kw):
                vals = [r[i + 1] for r in y5]
                last = sum(vals[-2:]) / 2
                item["y5"].append({"kw": k, "last": round(last), "pct": round(100 * sum(1 for v in vals if v <= last) / len(vals))})
        out[name] = item
    return out


def run() -> dict:
    t0, errs = time.time(), {}
    cache = _load()
    cache.setdefault("errs", {})
    T = trends(cache, cache["errs"])
    apps = None
    try:
        apps = app_ranks(errs)
    except Exception as ex:  # noqa: BLE001
        errs["apps"] = str(ex)[:120]
    W = {}
    try:
        W = wiki(cache, errs)
    except Exception as ex:  # noqa: BLE001
        errs["wiki"] = str(ex)[:120]
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    errs.update(cache["errs"])
    out = {"trends": _trend_view(T), "apps": apps, "wiki": W, "errors": errs, "runtime_sec": round(time.time() - t0, 1)}
    print(f"Chú ý nhỏ lẻ: Trends {list(out['trends'])}, ứng dụng {len((apps or {}).get('rows', []))} dòng, Wikipedia {list(W.get('series', {}))}, lỗi {list(errs)}")
    return out
