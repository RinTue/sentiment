"""Chu kỳ halving của Bitcoin: lưu giá đóng cửa theo ngày từ 2012 và ước tính ngày halving tới theo số khối.

- Trước 2018: giá thị trường theo ngày của blockchain.com (biểu đồ công khai market-price, không cần mã).
- Từ 2018: data/btc_daily.csv (giá đóng cửa ngày UTC, market.py cập nhật mỗi giờ).
- Halving tới: số khối hiện tại (blockchain.com) + thời gian khối thực tế 2016 khối gần nhất (nếu đọc được), mặc định 10 phút.
Việc tìm đáy, đỉnh, tỷ lệ và dự phóng do trang web tính từ dữ liệu này, để người xem đổi mốc và thấy ngay kết quả.
"""
from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "cycles.json")          # trang web đọc
CACHE = os.path.join(DATA, "cycles_cache.json")  # giá cũ và số khối, chỉ máy chủ dùng
UA = "sentiment-dashboard (github.com/RinTue/sentiment)"
DAY = 86400
START = int(datetime(2011, 6, 1, tzinfo=timezone.utc).timestamp())
HALVINGS = [("2012-11-28", 210000), ("2016-07-09", 420000), ("2020-05-11", 630000), ("2024-04-20", 840000)]
NEXT = [1050000, 1260000]


def _get(url, params=None, timeout=40):
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r


def old_prices() -> dict:
    j = _get("https://api.blockchain.info/charts/market-price", {"timespan": "all", "format": "json", "sampled": "false"}, 60).json()
    return {int(v["x"]) // DAY * DAY: float(v["y"]) for v in j.get("values", []) if v.get("y")}


def ohlc_bitstamp(since: int) -> dict:
    """Giá cao nhất / thấp nhất trong ngày (Bitstamp, sàn có lịch sử dài nhất), để đáy và đỉnh khớp số người ta hay dùng."""
    out, start = {}, since
    for _ in range(12):
        j = _get("https://www.bitstamp.net/api/v2/ohlc/btcusd/", {"step": 86400, "limit": 1000, "start": start}, 40).json()
        rows = (j.get("data") or {}).get("ohlc") or []
        if not rows:
            break
        for r in rows:
            out[int(r["timestamp"]) // DAY * DAY] = (float(r["high"]), float(r["low"]))
        nxt = int(rows[-1]["timestamp"]) + DAY
        if nxt <= start or len(rows) < 1000:
            break
        start = nxt
        time.sleep(0.5)
    if len(out) < 30:
        raise RuntimeError("Bitstamp: thiếu dữ liệu")
    return out


def ohlc_cryptocompare(since: int) -> dict:
    out, to = {}, int(time.time())
    for _ in range(4):
        j = _get("https://min-api.cryptocompare.com/data/v2/histoday", {"fsym": "BTC", "tsym": "USD", "limit": 2000, "toTs": to}, 40).json()
        rows = ((j.get("Data") or {}).get("Data")) or []
        if not rows:
            break
        for r in rows:
            if r.get("high"):
                out[int(r["time"]) // DAY * DAY] = (float(r["high"]), float(r["low"]))
        to = int(rows[0]["time"]) - DAY
        if to < since:
            break
        time.sleep(0.5)
    if len(out) < 30:
        raise RuntimeError("CryptoCompare: thiếu dữ liệu")
    return out


def ohlc(since: int) -> tuple[dict, str]:
    try:
        return ohlc_bitstamp(since), "Bitstamp"
    except Exception as ex:  # noqa: BLE001
        first = str(ex)[:100]
    try:
        return ohlc_cryptocompare(since), "CryptoCompare"
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(f"{first}; {str(ex)[:100]}") from None


def recent_prices() -> dict:
    out = {}
    with open(os.path.join(DATA, "btc_daily.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[int(r["day_utc"])] = float(r["close"])
    return out


def block_info() -> dict:
    h = int(_get("https://blockchain.info/q/getblockcount", timeout=20).text.strip())
    spb = 600.0
    try:   # thời gian khối thực tế: so thời điểm khối hiện tại với khối cách 2016 khối
        a = _get(f"https://blockchain.info/block-height/{h}", {"format": "json"}, 30).json()["blocks"][0]["time"]
        b = _get(f"https://blockchain.info/block-height/{h - 2016}", {"format": "json"}, 30).json()["blocks"][0]["time"]
        if a > b:
            spb = (a - b) / 2016
    except Exception:  # noqa: BLE001
        pass
    now = time.time()
    nxt = []
    adj = -(-h // 2016) * 2016          # mốc điều chỉnh độ khó kế tiếp; sau đó mạng tự kéo thời gian khối về 10 phút
    for target in NEXT:
        t = now + (adj - h) * spb + (target - adj) * 600
        nxt.append({"block": target, "date": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"),
                    "blocks_left": target - h, "est": True})
    return {"height": h, "sec_per_block": round(spb, 1), "adj": adj, "next": nxt}


def run() -> dict:
    try:
        old = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        old = {}
    errs = {}
    now = time.time()
    hist = old.get("_hist") or {}
    if not hist or now - old.get("fetched", 0) > 7 * DAY:      # giá cũ trước 2018 không đổi: tải lại mỗi tuần là đủ
        try:
            hist = {str(k): v for k, v in old_prices().items() if k >= START}
            old["fetched"] = now
        except Exception as ex:  # noqa: BLE001
            errs["blockchain_price"] = str(ex)[:150]
    hl = old.get("_hl") or {}
    hl_src = old.get("hl_src")
    try:
        full = not hl or now - old.get("hl_full", 0) > 7 * DAY
        got, hl_src = ohlc(START if full else int(now) - 60 * DAY)
        hl.update({str(k): [round(a, 2), round(b, 2)] for k, (a, b) in got.items() if k >= START})
        if full:
            old["hl_full"] = now
    except Exception as ex:  # noqa: BLE001
        errs["ohlc"] = str(ex)[:200]
    blk = old.get("blocks")
    if not blk or now - old.get("blocks_t", 0) > 6 * 3600 or "adj" not in blk:
        try:
            blk = block_info()
            old["blocks_t"] = now
        except Exception as ex:  # noqa: BLE001
            errs["blocks"] = str(ex)[:150]
    try:
        rec = recent_prices()
    except (OSError, ValueError, KeyError) as ex:
        rec = {}
        errs["btc_daily"] = str(ex)[:150]
    px = {int(k): v for k, v in hist.items()}
    first_rec = min(rec) if rec else None
    for d, v in rec.items():
        px[d] = v
    if not px:
        return {"error": "; ".join(f"{k}: {v}" for k, v in errs.items()) or "chưa có giá"}
    d0, d1 = min(px), max(px)
    arr, hi, lo, last = [], [], [], None
    rnd = lambda v: None if v is None else (round(v, 2) if v < 1000 else round(v))  # noqa: E731
    d = d0
    while d <= d1:            # lấp ngày trống bằng giá hôm trước
        v = px.get(d, last)
        arr.append(rnd(v))
        last = v if v is not None else last
        x = hl.get(str(d))
        hi.append(rnd(x[0]) if x else None)
        lo.append(rnd(x[1]) if x else None)
        d += DAY
    halv = [{"date": dt, "block": b} for dt, b in HALVINGS]
    if blk:
        halv += [{"date": n["date"], "block": n["block"], "est": True} for n in blk["next"]]
    out = {"start": datetime.fromtimestamp(d0, timezone.utc).strftime("%Y-%m-%d"), "close": arr, "hi": hi, "lo": lo, "hl_src": hl_src, "halvings": halv,
           "blocks": blk, "split": datetime.fromtimestamp(first_rec, timezone.utc).strftime("%Y-%m-%d") if first_rec else None,
           "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": errs,
           "source": "blockchain.com (giá trung bình theo ngày, trước " + (datetime.fromtimestamp(first_rec, timezone.utc).strftime("%d/%m/%Y") if first_rec else "?") + "), giá đóng cửa ngày UTC sau đó"}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"_hist": hist, "fetched": old.get("fetched", 0), "blocks": blk, "blocks_t": old.get("blocks_t", 0),
                   "_hl": hl, "hl_src": hl_src, "hl_full": old.get("hl_full", 0)}, f, separators=(",", ":"))
    return {"start": out["start"], "days": len(arr), "hl_days": sum(1 for x in hi if x), "hl_src": hl_src, "blocks": blk, "errors": errs}
