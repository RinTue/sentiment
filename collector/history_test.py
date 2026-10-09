"""Kiểm chứng bằng lịch sử từ 2022 (chạy mỗi ngày một lần).

1. Dựng lại từng chỉ báo theo ngày: Fear & Greed, funding và open interest tổng 3 sàn lớn (Binance, Bybit, OKX qua Coinalyze),
   thanh lý, Coinbase Premium, tổng stablecoin, lượt xem Wikipedia.
2. Dựng lại chỉ số tổng hợp theo đúng công thức hiện tại với những thành phần có lịch sử (Fear & Greed, funding, Wikipedia).
3. Với mỗi chỉ báo: chia ngày thành 5 nhóm theo mức (thấp → cao), xem BTC 7 và 30 ngày sau đi thế nào.
4. Chạy lại phân loại "đà giá đến từ đâu" cho từng ngày, xem mỗi kiểu thường tiếp diễn hay đảo chiều.

Mọi phân vị đều tính trên dữ liệu quá khứ của chính ngày đó (không nhìn trước tương lai).
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests

import market

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT_FILE = os.path.join(DATA, "history_test.json")
START = datetime(2022, 1, 1, tzinfo=timezone.utc)
UA = market.UA
DAY = 86400


def day_of(ts: float) -> int:
    return int(ts // DAY * DAY)


def get(url: str, params: dict | None = None, timeout: int = 30):
    r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- dữ liệu
def btc_close() -> dict:
    with open(os.path.join(DATA, "btc_daily.csv"), encoding="utf-8") as f:
        return {int(r[0]): float(r[1]) for r in list(csv.reader(f))[1:]}


def fng_hist() -> dict:
    return {day_of(t): float(v) for t, v, _ in market.fng(0)}


def wiki_hist() -> dict:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    j = get(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Bitcoin/daily/20211001/{end:%Y%m%d}")
    return {int(datetime.strptime(d["timestamp"][:8], "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()): float(d["views"]) for d in j["items"]}


def stable_hist() -> dict:
    return {day_of(t): v for t, v in market.stablecoins()}


def okx_spot_daily() -> dict:
    out, after = {}, None
    for _ in range(25):
        p = {"instId": "BTC-USDT", "bar": "1Dutc", "limit": 100}
        if after:
            p["after"] = after
        j = get("https://www.okx.com/api/v5/market/history-candles", p)
        rows = j.get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0]) // 1000] = float(r[4])
        after = rows[-1][0]
        if int(after) // 1000 < START.timestamp() - 60 * DAY:
            break
        time.sleep(0.2)
    return out


def coinalyze_daily() -> dict:
    """Funding (bình quân theo OI), OI (BTC) và thanh lý theo ngày của hợp đồng USDT vĩnh cửu trên Binance, Bybit, OKX."""
    if not market.COINALYZE_KEY:
        return {}
    syms = ["BTCUSDT_PERP.A", "BTCUSDT.6", "BTCUSDT_PERP.3"]
    try:
        c = json.load(open(market.COINALYZE_CACHE, encoding="utf-8"))
        pick = [s for s in c.get("symbols", []) if "USDT" in s and c.get("ex_of", {}).get(s) in ("Binance", "Bybit", "OKX")]
        if len(pick) >= 2:
            syms = pick[:3]
    except (OSError, json.JSONDecodeError):
        pass
    p = {"symbols": ",".join(syms), "interval": "daily", "from": int(START.timestamp()) - 120 * DAY, "to": int(time.time())}
    oi = market.cz_get("open-interest-history", dict(p, convert_to_usd="true"))
    time.sleep(2)
    fr = market.cz_get("funding-rate-history", p)
    time.sleep(2)
    lq = market.cz_get("liquidation-history", dict(p, convert_to_usd="true"))
    oi_by: dict = {}
    for row in oi:
        for h in row.get("history", []):
            oi_by.setdefault(day_of(h["t"]), {})[row["symbol"]] = float(h.get("c") or 0)
    full = max((len(v) for v in oi_by.values()), default=0)
    oi_usd = {d: sum(v.values()) for d, v in oi_by.items() if len(v) == full}
    num_: dict = {}
    den_: dict = {}
    raw = []
    for row in fr:
        for h in row.get("history", []):
            d = day_of(h["t"])
            w = oi_by.get(d, {}).get(row["symbol"])
            if not w:
                continue
            v = float(h.get("c") or 0)
            raw.append(abs(v))
            num_[d] = num_.get(d, 0.0) + v * w
            den_[d] = den_.get(d, 0.0) + w
    scale = 0.01 if raw and sorted(raw)[len(raw) // 2] > 0.003 else 1.0
    fund = {d: num_[d] / den_[d] * scale for d in num_ if den_[d]}
    liq: dict = {}
    for row in lq:
        for h in row.get("history", []):
            a = liq.setdefault(day_of(h["t"]), [0.0, 0.0])
            a[0] += float(h.get("l") or 0)
            a[1] += float(h.get("s") or 0)
    return {"oi_usd": oi_usd, "funding": fund, "liq": liq, "symbols": syms}


# ---------------------------------------------------------------- tính toán
def trailing_pct(series: dict, window: int, min_n: int = 30) -> dict:
    """Phân vị của giá trị hôm nay so với `window` ngày trước đó (chỉ dùng quá khứ)."""
    days = sorted(series)
    out = {}
    for i, d in enumerate(days):
        past = [series[x] for x in days[max(0, i - window):i + 1]]
        if len(past) >= min_n:
            v = series[d]
            out[d] = 100 * sum(1 for x in past if x <= v) / len(past)
    return out


def rolling(series: dict, fn, n: int) -> dict:
    days = sorted(series)
    return {days[i]: fn([series[x] for x in days[i - n + 1:i + 1]]) for i in range(n - 1, len(days))}


def change(series: dict, n: int) -> dict:
    return {d: (v / series[d - n * DAY] - 1) * 100 for d, v in series.items() if series.get(d - n * DAY)}


def fwd(px: dict, n: int) -> dict:
    return {d: (px[d + n * DAY] / v - 1) * 100 for d, v in px.items() if d + n * DAY in px}


def spearman(a: list, b: list) -> float | None:
    if len(a) < 30:
        return None
    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        for k, i in enumerate(order):
            r[i] = k
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return round(cov / (va * vb), 3) if va and vb else None


def bucket_table(score: dict, f7: dict, f30: dict) -> dict:
    """Chia ngày theo điểm 0–100 thành 5 nhóm; trả về thống kê lợi nhuận BTC sau 7 và 30 ngày."""
    rows = []
    for lo in (0, 20, 40, 60, 80):
        hi = 101 if lo == 80 else lo + 20
        d7 = [f7[d] for d, v in score.items() if lo <= v < hi and d in f7]
        d30 = [f30[d] for d, v in score.items() if lo <= v < hi and d in f30]
        rows.append({"lo": lo, "hi": min(hi, 100), "n": len(d7),
                     "up7": round(100 * sum(1 for x in d7 if x > 0) / len(d7)) if d7 else None, "med7": round(statistics.median(d7), 1) if d7 else None,
                     "up30": round(100 * sum(1 for x in d30 if x > 0) / len(d30)) if d30 else None, "med30": round(statistics.median(d30), 1) if d30 else None})
    common = [d for d in score if d in f30]
    rho = spearman([score[d] for d in common], [f30[d] for d in common])
    lo_b, hi_b = rows[0], rows[-1]
    spread = round(hi_b["med30"] - lo_b["med30"], 1) if hi_b["med30"] is not None and lo_b["med30"] is not None else None
    return {"bins": rows, "rho30": rho, "spread30": spread, "days": len(common)}


SPLIT = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()


def favor_score(F: dict, codes: dict, f7: dict, f30: dict, names: dict) -> dict | None:
    """Điểm thuận lợi: học từ 2022–2024 xem mỗi mức của mỗi chỉ báo thường đi trước BTC tăng hay giảm (30 ngày),
    rồi kiểm tra lại trên 2025–nay, giai đoạn mà quy tắc chưa từng thấy."""
    keys = [k for k in ("stable30", "liq7", "prem7", "wiki", "fng", "funding", "oi7") if k in F]
    if len(keys) < 3:
        return None
    def bin_of(v):
        return min(4, int(v // 20))
    train_days = [d for d in f30 if d < SPLIT]
    base_up = sum(1 for d in train_days if f30[d] > 0) / len(train_days)
    edges = {}
    for k in keys:
        e = {}
        for b in range(5):
            sel = [d for d in train_days if d in F[k] and bin_of(F[k][d]) == b]
            if len(sel) >= 15:
                up = sum(1 for d in sel if f30[d] > 0) / len(sel)
                e[b] = (up - base_up) * len(sel) / (len(sel) + 60)     # co lại khi ít ngày
        edges[k] = e
    # kiểu đà giá theo ngày (7 ngày sau), cộng với trọng số một nửa
    ce = {}
    base7 = sum(1 for d in f7 if d < SPLIT and f7[d] > 0) / max(1, sum(1 for d in f7 if d < SPLIT))
    for c in {v[0] for v in codes.values()}:
        sel = [d for d, v in codes.items() if v[0] == c and d < SPLIT and d in f7]
        if len(sel) >= 10 and c != "flat":
            up = sum(1 for d in sel if f7[d] > 0) / len(sel)
            ce[c] = 0.5 * (up - base7) * len(sel) / (len(sel) + 30)
    days = sorted(set().union(*[set(F[k]) for k in keys]))
    raw = {}
    for d in days:
        if sum(1 for k in keys if d in F[k]) < len(keys) - 1:
            continue
        r = sum(edges[k].get(bin_of(F[k][d]), 0) for k in keys if d in F[k])
        last_code = codes.get(d, ("flat", ""))[0]
        r += ce.get(last_code, 0)
        raw[d] = r
    train_raw = sorted(v for d, v in raw.items() if d < SPLIT)
    if len(train_raw) < 200:
        return None
    score = {d: 100 * sum(1 for x in train_raw if x <= v) / len(train_raw) for d, v in raw.items()}
    def zone(v):
        return "Thuận lợi" if v >= 70 else "Bất lợi" if v <= 30 else "Trung tính"
    def table(sel):
        out = []
        for lo, hi, name in ((0, 30.0001, "Bất lợi (0–30)"), (30.0001, 70, "Trung tính (30–70)"), (70, 101, "Thuận lợi (70–100)")):
            d30 = [f30[d] for d in sel if lo <= score[d] < hi and d in f30]
            d7 = [f7[d] for d in sel if lo <= score[d] < hi and d in f7]
            out.append({"name": name, "n": len(d30), "up30": round(100 * sum(1 for x in d30 if x > 0) / len(d30)) if d30 else None,
                        "med30": round(statistics.median(d30), 1) if d30 else None, "up7": round(100 * sum(1 for x in d7 if x > 0) / len(d7)) if d7 else None})
        allv = [f30[d] for d in sel if d in f30]
        out.append({"name": "Mọi ngày", "n": len(allv), "up30": round(100 * sum(1 for x in allv if x > 0) / len(allv)) if allv else None,
                    "med30": round(statistics.median(allv), 1) if allv else None, "up7": None})
        return out
    tr = [d for d in score if d < SPLIT]
    te = [d for d in score if d >= SPLIT]
    last = max(score)
    parts = []
    for k in keys:
        if last in F[k]:
            b = bin_of(F[k][last])
            parts.append({"key": k, "name": names.get(k, k), "level": round(F[k][last]), "edge": round(100 * edges[k].get(b, 0), 1)})
    lc = codes.get(last)
    if lc and lc[0] in ce:
        parts.append({"key": "drv", "name": "Kiểu đà giá hôm qua: " + lc[1], "level": None, "edge": round(100 * ce[lc[0]], 1)})
    parts.sort(key=lambda x: -abs(x["edge"]))
    return {"value": round(score[last]), "zone": zone(score[last]), "asof": datetime.fromtimestamp(last, timezone.utc).strftime("%Y-%m-%d"),
            "parts": parts, "train": table(tr), "test": table(te), "split": "2025-01-01",
            "series": [[int(d * 1000), round(score[d])] for d in sorted(score) if d >= last - 365 * DAY]}


def run(force: bool = False) -> dict | None:
    old = None
    try:
        old = json.load(open(OUT_FILE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if old and old.get("day") == today and "favor" in old and not force:
        return old
    t0, errs = time.time(), {}
    def safe(name, fn):
        try:
            return fn()
        except Exception as ex:  # noqa: BLE001
            errs[name] = str(ex)[:120]
            return {}
    px = btc_close()
    f7, f30 = fwd(px, 7), fwd(px, 30)
    fng = safe("fng", fng_hist)
    wiki = safe("wiki", wiki_hist)
    stab = safe("stable", stable_hist)
    okx = safe("okx", okx_spot_daily)
    time.sleep(61)   # nhường lượt gọi Coinalyze cho phần chạy mỗi giờ
    cz = safe("coinalyze", coinalyze_daily)
    start = START.timestamp()
    keep = lambda s: {d: v for d, v in s.items() if d >= start}  # noqa: E731

    F = {}   # điểm 0–100 của từng chỉ báo theo ngày
    if fng:
        F["fng"] = keep(fng)
    if cz.get("funding"):
        F["funding"] = keep(trailing_pct(cz["funding"], 90))
    if wiki:
        days = sorted(wiki)
        wz = {}
        for i in range(63, len(days)):
            v = [wiki[days[k]] for k in range(i - 62, i + 1)]
            last, prior = v[-3:], v[:-3]
            m = sum(prior) / len(prior)
            sd = math.sqrt(sum((x - m) ** 2 for x in prior) / len(prior)) or 1
            wz[days[i]] = max(0.0, min(100.0, 50 + 25 * ((sum(last) / 3 - m) / sd)))
        F["wiki"] = keep(wz)
    oi_btc = {d: v / px[d] for d, v in (cz.get("oi_usd") or {}).items() if px.get(d)}
    if oi_btc:
        F["oi7"] = keep(trailing_pct(change(oi_btc, 7), 365))
    if cz.get("liq"):
        imb = {d: (s - l) / (s + l) for d, (l, s) in cz["liq"].items() if s + l > 0}
        F["liq7"] = keep({d: (v + 1) * 50 for d, v in rolling(imb, lambda a: sum(a) / len(a), 7).items()})
    prem = {d: (px[d] / okx[d] - 1) * 100 for d in okx if px.get(d)}
    if prem:
        F["prem7"] = keep(trailing_pct(rolling(prem, lambda a: sum(a) / len(a), 7), 365))
    if stab:
        F["stable30"] = keep(trailing_pct(change(stab, 30), 365))

    # Chỉ số tổng hợp dựng lại: đúng công thức trang web với các thành phần có lịch sử
    W = {"fng": 20, "funding": 20, "wiki": 15}
    comp = {}
    for d in F.get("fng", {}):
        parts = [(F[k][d], w) for k, w in W.items() if k in F and d in F[k]]
        if len(parts) == len(W):
            comp[d] = sum(v * w for v, w in parts) / sum(w for _, w in parts)
    NAMES = {"composite": "Chỉ số tổng hợp (dựng lại: Fear & Greed, funding, Wikipedia)", "fng": "Fear & Greed",
             "funding": "Funding (phân vị 90 ngày)", "wiki": "Chú ý Wikipedia", "oi7": "Open interest thay đổi 7 ngày",
             "liq7": "Thanh lý 7 ngày (cao = short bị thanh lý nhiều)", "prem7": "Coinbase Premium 7 ngày", "stable30": "Stablecoin thay đổi 30 ngày"}
    factors = []
    for k, series in [("composite", comp)] + list(F.items()):
        if len(series) < 200:
            continue
        t = bucket_table(series, f7, f30)
        factors.append({"key": k, "name": NAMES.get(k, k), **t})
    base = {"n": len(f30), "up7": round(100 * sum(1 for x in f7.values() if x > 0) / len(f7)) if f7 else None,
            "up30": round(100 * sum(1 for x in f30.values() if x > 0) / len(f30)) if f30 else None,
            "med7": round(statistics.median(f7.values()), 1) if f7 else None, "med30": round(statistics.median(f30.values()), 1) if f30 else None}

    # Phân loại đà giá theo ngày
    drv = []
    codes: dict = {}
    if oi_btc and cz.get("funding"):
        fpct = trailing_pct(cz["funding"], 90)
        f1, f7d = fwd(px, 1), f7
        by: dict = {}
        for d in sorted(oi_btc):
            if d < start or d - DAY not in px or d - DAY not in oi_btc or d not in px:
                continue
            dp = (px[d] / px[d - DAY] - 1) * 100
            doi = (oi_btc[d] / oi_btc[d - DAY] - 1) * 100
            l, s = (cz.get("liq") or {}).get(d, (0.0, 0.0))
            pr = prem.get(d)
            code, label, _ = market.classify_move(dp, doi, fpct.get(d), None, None, pr, {"long": l / 5, "short": s / 5}, 3.0, 2.0)
            codes[d] = (code, label)
            if d not in f7d:
                continue        # ngày gần đây chưa có kết quả 7 ngày sau: chỉ dùng để chấm điểm hôm nay
            by.setdefault(code, {"label": label, "r1": [], "r7": [], "cont": []})
            g = by[code]
            if d in f1:
                g["r1"].append(f1[d])
            g["r7"].append(f7d[d])
            if abs(dp) >= 3:
                g["cont"].append(1 if (f7d[d] > 0) == (dp > 0) else 0)
        for code, g in by.items():
            if len(g["r7"]) < 5:
                continue
            drv.append({"code": code, "label": g["label"], "n": len(g["r7"]),
                        "med1": round(statistics.median(g["r1"]), 2) if g["r1"] else None, "med7": round(statistics.median(g["r7"]), 1),
                        "up7": round(100 * sum(1 for x in g["r7"] if x > 0) / len(g["r7"])),
                        "cont7": round(100 * sum(g["cont"]) / len(g["cont"])) if g["cont"] else None})
        drv.sort(key=lambda x: -x["n"])
    favor = None
    try:
        favor = favor_score(F, codes, f7, f30, NAMES)
    except Exception as ex:  # noqa: BLE001
        errs["favor"] = str(ex)[:120]
    out = {"day": today, "start": START.strftime("%Y-%m-%d"), "base": base, "factors": factors, "drivers": drv, "favor": favor,
           "symbols": cz.get("symbols"), "errors": errs, "runtime_sec": round(time.time() - t0, 1)}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"Kiểm chứng lịch sử: {len(factors)} chỉ báo, {len(drv)} kiểu đà giá, lỗi {list(errs)}, {out['runtime_sec']} giây")
    return out
