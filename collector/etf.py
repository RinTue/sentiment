"""Dòng tiền ETF Bitcoin giao ngay ở Mỹ.

Nguồn: The Commoner Bitcoin ETF Tracker của TFTC (tftc.io/bitcoin-etf-flows), giấy phép CC BY 4.0,
không cần mã API. Họ tổng hợp từ SoSoValue và số công bố của các quỹ (Farside), có từ 11/1/2024.
Số ngày T có sau khi thị trường Mỹ đóng cửa (sáng T+1 giờ Việt Nam).

Lưu data/etf.json (toàn bộ lịch sử, gọn) để trang vẽ biểu đồ; trả về bản tóm tắt cho latest.json.
Lỗi mạng không làm hỏng lần chạy: giữ bản đã lưu và ghi lỗi.
"""
from __future__ import annotations

import csv
import json
import os
import statistics as stx
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "etf.json")
URL_DATA = "https://www.tftc.io/bitcoin-etf-flows/data.json"
URL_LATEST = "https://www.tftc.io/bitcoin-etf-flows/latest.json"
UA = "sentiment-dashboard (github.com/RinTue/sentiment)"
REFRESH_SEC = 2 * 3600          # dữ liệu chỉ đổi mỗi ngày một lần; không cần tải lại mỗi giờ
STREAK_N = 4                    # chuỗi từ 4 phiên cùng chiều trở lên mới tính là "chuỗi"


def _load() -> dict:
    try:
        return json.load(open(OUT, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get(url: str) -> dict:
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def _row(d: dict) -> list:
    """[ngày, tiền ròng (triệu $), tổng tài sản (tỷ $) hoặc None, giá BTC đóng cửa hoặc None]"""
    f = d.get("netFlowUsd")
    if f is None:
        f = d.get("vendorNetFlowUsd") or 0
    a = d.get("totalNetAssetsUsd")
    p = d.get("btcCloseUsd")
    return [d["date"], round(f / 1e6, 1), round(a / 1e9, 2) if a else None, round(p) if p else None]


def _funds(d: dict) -> dict:
    return {k: round(v / 1e6, 1) for k, v in (d.get("perEtfUsd") or {}).items() if v}


def fetch(old: dict) -> dict:
    """Tải toàn bộ lịch sử; nếu lỗi thì thử tệp phiên mới nhất và ghép vào bản đã lưu."""
    try:
        J = _get(URL_DATA)
        days = sorted((x for x in J.get("days") or [] if x.get("date")), key=lambda x: x["date"])
        if len(days) < 100:
            raise RuntimeError(f"chỉ có {len(days)} ngày")
        return {"days": [_row(d) for d in days],
                "funds": {d["date"]: _funds(d) for d in days[-130:] if d.get("perEtfUsd")},
                "src_time": J.get("dateModified"), "through": J.get("updatedThrough") or days[-1]["date"], "via": "data.json"}
    except Exception as ex:  # noqa: BLE001
        err = f"data.json: {str(ex)[:120]}"
    try:
        J = _get(URL_LATEST)
        rows = {r[0]: r for r in old.get("days") or []}
        funds = dict(old.get("funds") or {})
        for s in J.get("recentSessions") or []:
            if s.get("date") and s["date"] not in rows:
                rows[s["date"]] = [s["date"], round((s.get("netFlowUsd") or 0) / 1e6, 1), None, None]
        L = J.get("latestSession") or {}
        if L.get("date"):
            rows[L["date"]] = _row(L)
            if L.get("perEtfUsd"):
                funds[L["date"]] = _funds(L)
        if not rows:
            raise RuntimeError("trống")
        return {"days": [rows[k] for k in sorted(rows)], "funds": dict(sorted(funds.items())[-130:]),
                "src_time": J.get("dateModified"), "through": J.get("updatedThrough"), "via": "latest.json", "warn": err}
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(f"{err}; latest.json: {str(ex)[:120]}") from None


def _prices() -> dict:
    """Giá đóng cửa ngày UTC từ data/btc_daily.csv (market.py đã cập nhật trước đó)."""
    out = {}
    try:
        with open(os.path.join(DATA, "btc_daily.csv"), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[datetime.fromtimestamp(int(r["day_utc"]), timezone.utc).strftime("%Y-%m-%d")] = float(r["close"])
    except (OSError, ValueError, KeyError):
        pass
    return out


def _fwd(px: dict, day: str, n: int):
    d0 = datetime.strptime(day, "%Y-%m-%d")
    p0, p1 = px.get(day), px.get((d0 + timedelta(days=n)).strftime("%Y-%m-%d"))
    return (p1 / p0 - 1) * 100 if p0 and p1 else None


def _stat(xs: list) -> dict | None:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"n": len(xs), "up": round(100 * sum(x > 0 for x in xs) / len(xs)), "med": round(stx.median(xs), 1)}


def history(days: list) -> dict:
    """Sau những lúc tương tự hiện nay, giá BTC 7 và 30 ngày sau đi thế nào (từ 2024)."""
    px = _prices()
    if not px:
        return {}
    flows = [r[1] for r in days]
    out_ev, in_ev, big_ev, base = [], [], [], []
    run = 0
    srt = sorted(flows)
    p5 = srt[max(0, int(len(srt) * 0.05) - 1)]          # 5% phiên rút mạnh nhất
    for i, r in enumerate(days):
        f = r[1]
        run = (run + 1 if run > 0 else 1) if f > 0 else (run - 1 if run < 0 else -1) if f < 0 else 0
        base.append(r[0])
        if run == -STREAK_N:
            out_ev.append(r[0])
        if run == STREAK_N:
            in_ev.append(r[0])
        if f <= p5:
            big_ev.append(r[0])

    def S(ev):
        a, b = _stat([_fwd(px, d, 7) for d in ev]), _stat([_fwd(px, d, 30) for d in ev])
        return {"d7": a, "d30": b} if a else None
    return {"streak_n": STREAK_N, "out": S(out_ev), "in": S(in_ev), "big_out": S(big_ev), "big_thr": round(p5), "base": S(base),
            "since": days[0][0] if days else None}


def summarize(days: list, funds: dict) -> dict:
    if not days:
        return {}
    last = days[-1]
    flows = [r[1] for r in days]
    n, sgn = 0, (1 if last[1] > 0 else -1 if last[1] < 0 else 0)
    tot = 0.0
    for f in reversed(flows):
        if sgn and (f > 0) == (sgn > 0) and f != 0:
            n += 1
            tot += f
        else:
            break
    dl = datetime.strptime(last[0], "%Y-%m-%d")

    def cal(k):   # tổng theo ngày lịch (giống các trang theo dõi: 7D, 30D)
        lo = (dl - timedelta(days=k - 1)).strftime("%Y-%m-%d")
        return round(sum(r[1] for r in days if r[0] >= lo), 1)
    yr = [abs(f) for f in flows[-252:]]
    rank = round(100 * sum(x <= abs(last[1]) for x in yr) / len(yr)) if yr else None
    fl = funds.get(last[0]) or {}
    top = sorted(fl.items(), key=lambda kv: -abs(kv[1]))[:3]
    f5: dict = {}
    for r in days[-5:]:
        for k, v in (funds.get(r[0]) or {}).items():
            f5[k] = round(f5.get(k, 0) + v, 1)
    assets = next((r[2] for r in reversed(days) if r[2]), None)
    return {"date": last[0], "flow": last[1], "streak": {"n": n, "sign": sgn, "sum": round(tot, 1)},
            "s5": round(sum(flows[-5:]), 1), "s20": round(sum(flows[-20:]), 1), "d7": cal(7), "d30": cal(30),
            "rank": rank, "top": top, "top5": sorted(f5.items(), key=lambda kv: -abs(kv[1]))[:4], "assets": assets,
            "cum": round(sum(flows) / 1e3, 2), "n_days": len(days)}


def run() -> dict:
    old = _load()
    now = time.time()
    data = old
    err = None
    if not old.get("days") or now - old.get("fetched", 0) >= REFRESH_SEC:
        try:
            data = fetch(old)
            data["fetched"] = now
        except Exception as ex:  # noqa: BLE001
            err = str(ex)[:300]
            data = dict(old)
    if not data.get("days"):
        return {"error": err or "chưa có dữ liệu"}
    data["summary"] = summarize(data["days"], data.get("funds") or {})
    data["hist"] = history(data["days"])
    data["err"] = err
    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["source"] = "The Commoner Bitcoin ETF Tracker by TFTC, tftc.io/bitcoin-etf-flows (CC BY 4.0)"
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    out = {k: data.get(k) for k in ("summary", "hist", "through", "src_time", "via", "err", "warn", "fetched")}
    return out
