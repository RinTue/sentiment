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
    for target in NEXT:
        t = now + (target - h) * spb
        nxt.append({"block": target, "date": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"),
                    "blocks_left": target - h, "est": True})
    return {"height": h, "sec_per_block": round(spb, 1), "next": nxt}


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
    blk = old.get("blocks")
    if not blk or now - old.get("blocks_t", 0) > 6 * 3600:
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
    arr, last = [], None
    d = d0
    while d <= d1:            # lấp ngày trống bằng giá hôm trước
        v = px.get(d, last)
        arr.append(None if v is None else (round(v, 2) if v < 1000 else round(v)))
        last = v if v is not None else last
        d += DAY
    halv = [{"date": dt, "block": b} for dt, b in HALVINGS]
    if blk:
        halv += [{"date": n["date"], "block": n["block"], "est": True} for n in blk["next"]]
    out = {"start": datetime.fromtimestamp(d0, timezone.utc).strftime("%Y-%m-%d"), "close": arr, "halvings": halv,
           "blocks": blk, "split": datetime.fromtimestamp(first_rec, timezone.utc).strftime("%Y-%m-%d") if first_rec else None,
           "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": errs,
           "source": "blockchain.com (giá trung bình theo ngày, trước " + (datetime.fromtimestamp(first_rec, timezone.utc).strftime("%d/%m/%Y") if first_rec else "?") + "), giá đóng cửa ngày UTC sau đó"}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"_hist": hist, "fetched": old.get("fetched", 0), "blocks": blk, "blocks_t": old.get("blocks_t", 0)}, f, separators=(",", ":"))
    return {"start": out["start"], "days": len(arr), "blocks": blk, "errors": errs}
