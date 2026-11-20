"""Bối cảnh thị trường: bổ trợ cho phân tích kỹ thuật bằng dữ liệu ngoài biểu đồ giá.

Đọc data/research_features.csv (bảng chỉ số theo ngày, đã dời theo độ trễ công bố, do research.py dựng) và
data/cycles.json (giá đóng cửa ngày), ghi data/context.json cho trang Tổng quan và cho Telegram.

Gồm:
- 3 điểm 0–100 (thứ hạng so với lịch sử): Vĩ mô, Cá mập / tiền lớn, Định giá on-chain.
- Pha tâm lý đám đông theo lý thuyết Dow: Chán nản (vùng gom), Hồi phục, Hưng phấn, Suy yếu, Hoảng loạn.
- 2 kết luận theo tầm nhìn (1–3 tháng, 1–2 năm) và 3 dấu hiệu xu hướng đổi chiều đi lên.
- Bảng kiểm chứng: ở mỗi mức điểm / mỗi pha, giá BTC sau đó đi thế nào (tính lại mỗi lần chạy, toàn bộ lịch sử).

Các ngưỡng là quy tắc cố định, ghi rõ trong mã để ai cũng kiểm tra được. Không dùng đường trung bình giá ngắn hạn.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CSV = os.path.join(DATA, "research_features.csv")
OUT = os.path.join(DATA, "context.json")
V = 1

# nhóm -> {chỉ số: chiều (+1: cao là tốt cho BTC / đắt hơn với định giá)}
GROUPS = {
    "macro": {"usd": -1, "real": -1, "path": -1, "netliq": 1, "hy": -1, "vix": -1},
    "whale": {"etf20": 1, "spotnet": 1, "prem": 1, "stable": 1, "exbal": -1, "lth30": 1, "netout": 1},
    "value": {"mvrv": 1, "sthmvrv": 1, "rpl": 1},
}
WIN = {"macro": 365, "whale": 365, "value": 1460}     # cửa sổ xếp hạng (ngày)
NAMES = {"macro": "Vĩ mô", "whale": "Cá mập / tiền lớn", "value": "Định giá on-chain"}
ITEM = {   # tên hiển thị và cách viết giá trị
    "usd": ("Đồng đô la Mỹ, thay đổi 3 tháng", lambda x: f"{x*100:+.1f}%"),
    "real": ("Lợi suất thực 10 năm, thay đổi 3 tháng", lambda x: f"{x:+.2f} điểm %"),
    "path": ("Kỳ vọng lãi Fed (lợi suất 2 năm − lãi Fed)", lambda x: f"{x:+.2f} điểm %"),
    "netliq": ("Thanh khoản ròng của Fed, 13 tuần", lambda x: f"{x*100:+.1f}%"),
    "hy": ("Chênh lệch trái phiếu rủi ro cao", lambda x: f"{x:.2f}%"),
    "vix": ("VIX (sợ hãi chứng khoán Mỹ)", lambda x: f"{x:.1f}"),
    "etf20": ("ETF Bitcoin Mỹ, tổng 20 phiên", lambda x: f"{x/1000:+.2f} tỷ $" if abs(x) >= 1000 else f"{x:+.0f} triệu $"),
    "spotnet": ("Lực mua giao ngay ròng, 30 ngày", lambda x: f"{x*100:+.1f}% khối lượng"),
    "prem": ("Coinbase Premium (người Mỹ), 7 ngày", lambda x: f"{x:+.2f}%"),
    "stable": ("Nguồn stablecoin, 30 ngày", lambda x: f"{(np.exp(x)-1)*100:+.1f}%"),
    "exbal": ("BTC nằm trên sàn, 30 ngày (giảm là tốt)", lambda x: f"{(np.exp(x)-1)*100:+.1f}%"),
    "lth30": ("Tỷ lệ cung của người giữ lâu năm, 30 ngày", lambda x: f"{x:+.2f} điểm %"),
    "netout": ("BTC rút ròng khỏi sàn, trung bình 30 ngày", lambda x: f"{x:+,.0f} BTC/ngày"),
    "mvrv": ("MVRV (giá ÷ giá vốn thực)", lambda x: f"{np.exp(x):.2f} lần"),
    "sthmvrv": ("MVRV của người mới mua (dưới 155 ngày)", lambda x: f"{np.exp(x):.2f} lần"),
    "rpl": ("Lãi ÷ lỗ đã chốt trên chuỗi, 7 ngày", lambda x: f"{np.exp(x):.2f} lần"),
}
TESTED = {"real", "spotnet"}     # qua được kiểm tra walk-forward của research.py
PHASES = {   # khoá -> (tên, mô tả, màu)
    "apa": ("Chán nản", "Giá rẻ, đi ngang, biến động cạn, không ai quan tâm. Theo Dow đây là vùng gom.", "#3fb950"),
    "rec": ("Hồi phục", "Giá rời vùng rẻ, đám đông dần quay lại.", "#58a6ff"),
    "eu": ("Hưng phấn", "Giá đắt, tham lam cực độ.", "#f85149"),
    "wk": ("Suy yếu", "Giá đã giảm hơn 25% từ đỉnh nhưng chưa rẻ.", "#db6d28"),
    "pan": ("Hoảng loạn", "Giá rẻ nhưng còn rơi mạnh, cực sợ, bán tháo.", "#a371f7"),
}
ORDER = ["apa", "rec", "eu", "wk", "pan"]
# Ngưỡng pha (cố định)
CHEAP = 1.15       # giá ÷ trung bình 200 tuần dưới mức này là vùng rẻ
EUPH_W = 2.2       # trên mức này là vùng hưng phấn
VOL_PANIC = 60     # hạng biến động 30 ngày (so với 2 năm) từ mức này trở lên là hoảng loạn
FNG_PANIC = 25     # Fear & Greed dưới mức này là cực sợ
DD_WEAK = -0.25    # cách đỉnh hơn 25% là suy yếu
SIG_MACRO, SIG_WHALE = 40, 60


def vn(t: str) -> str:
    """Viết số kiểu Việt Nam: dấu phẩy thập phân, dấu chấm hàng nghìn."""
    return t.replace(",", "\0").replace(".", ",").replace("\0", ".").replace("-", "−")


def prank(s: pd.Series, w: int) -> pd.Series:
    """Thứ hạng % của giá trị hôm nay so với w ngày trước đó (chỉ dùng quá khứ)."""
    a = s.to_numpy(dtype=float)
    out = np.full(len(a), np.nan)
    mp = min(365, max(180, w // 2))
    for i in range(len(a)):
        if not np.isfinite(a[i]):
            continue
        h = a[max(0, i - w):i]
        h = h[np.isfinite(h)]
        if len(h) >= mp:
            out[i] = (h < a[i]).mean() * 100
    return pd.Series(out, index=s.index)


def load():
    X = pd.read_csv(CSV, parse_dates=["date"]).set_index("date")
    J = json.load(open(os.path.join(DATA, "cycles.json"), encoding="utf-8"))
    px = pd.Series(J["close"], index=pd.date_range(J["start"], periods=len(J["close"]), freq="D"), dtype=float).ffill()
    return X, px


def scores(X: pd.DataFrame):
    S, R = {}, {}
    for g, fs in GROUPS.items():
        cols = []
        for f, sgn in fs.items():
            if f not in X:
                continue
            r = prank(X[f].ffill(limit=10), WIN[g])
            R[f] = r if sgn > 0 else 100 - r
            cols.append(R[f])
        D = pd.concat(cols, axis=1)
        # cần ít nhất nửa số chỉ số có số liệu; định giá trước 2023 chỉ có MVRV (2 chỉ số kia mới có từ cuối 2022)
        need = 1 if g == "value" else max(2, int(np.ceil(D.shape[1] / 2)))
        m = D.mean(axis=1).where(D.notna().sum(axis=1) >= need)
        S[g] = m.rolling(3, min_periods=1).mean().where(m.notna())
    return pd.DataFrame(S), R


def phases(X: pd.DataFrame, px: pd.Series) -> pd.DataFrame:
    D = pd.DataFrame({"w": np.exp(X["w200"]), "dd": X["dd"], "fng": X["fng"].ffill(limit=5)}, index=X.index)
    D["volr"] = prank(X["vol30"], 730)
    D["rgr"] = prank(X["range60"], 730)
    D = D[D.index >= "2018-02-08"].dropna(subset=["w", "fng", "volr"])

    def one(r):
        if r.w >= EUPH_W or (r.fng >= 75 and r.w >= 1.5):
            return "eu"
        if r.w < CHEAP:
            return "pan" if (r.volr >= VOL_PANIC or r.fng < FNG_PANIC) else "apa"
        if r.dd <= DD_WEAK:
            return "wk"
        return "rec"
    raw = [one(r) for r in D.itertuples()]
    out = [raw[0]]
    for i in range(1, len(raw)):                 # đổi pha khi giữ nguyên 5 ngày liền, tránh nhảy qua lại
        w = raw[max(0, i - 4):i + 1]
        out.append(raw[i] if all(x == raw[i] for x in w) else out[-1])
    D["raw"], D["ph"] = raw, out
    D["px"] = px.reindex(D.index)
    return D


def fwd(px: pd.Series, h: int) -> pd.Series:
    return px.shift(-h) / px - 1


def bucket_stats(s: pd.Series, px: pd.Series, hs=(90, 365)) -> list:
    out = []
    F = {h: fwd(px, h).reindex(s.index) for h in hs}
    for lo in range(0, 100, 20):
        m = (s >= lo) & (s < lo + 20 if lo < 80 else s <= 100)
        row = {"lo": lo, "hi": lo + 20, "n": int(m.sum())}
        for h in hs:
            f = F[h][m].dropna()
            row[f"up{h}"] = round(float((f > 0).mean() * 100)) if len(f) >= 30 else None
            row[f"med{h}"] = round(float(f.median() * 100)) if len(f) >= 30 else None
        out.append(row)
    return out


# Dấu hiệu thoát hàng (Dow: cuối pha tăng, đám đông FOMO mua vào, tiền lớn phân phối)
EX_VALUE = 80        # điểm định giá từ mức này
EX_FNG, EX_FUND = 75, 85
EX_WHALE, EX_NEAR = 40, -0.15
EX_MACRO, EX_MDROP = 40, -20
TOPS = ["2021-04-14", "2021-11-10", "2024-03-14", "2025-10-06"]


def exits(S: pd.DataFrame, P: pd.DataFrame, X: pd.DataFrame, px: pd.Series) -> dict:
    D = P[["ph"]].join(S).join(px.rename("px"))
    D["fng"] = X["fng"].ffill(limit=5).reindex(D.index)
    D["fund"] = prank(X["funding"], 365).reindex(D.index) if "funding" in X else np.nan
    D["near"] = D.px / px.rolling(365).max().reindex(D.index) - 1
    D["mac60"] = D.macro - D.macro.shift(60)
    D["E1"] = D.value >= EX_VALUE
    D["E2"] = (D.fng >= EX_FNG) | (D.fund >= EX_FUND)
    D["E3"] = (D.whale <= EX_WHALE) & (D.near > EX_NEAR)
    D["E4"] = (D.macro <= EX_MACRO) | (D.mac60 <= EX_MDROP)
    E = ["E1", "E2", "E3", "E4"]
    D["k"] = D[E].sum(axis=1)
    f90 = fwd(px, 90).reindex(D.index)
    mn = px[::-1].rolling(91, min_periods=1).min()[::-1].reindex(D.index)      # giá thấp nhất trong 90 ngày tới
    D["dd90"] = mn / D.px - 1
    bull = D[D.ph.isin(["rec", "eu"]) & D.whale.notna() & D.value.notna() & D.macro.notna()]
    bull = bull[bull.index <= px.index[-1] - pd.Timedelta(days=90)]

    def st(g):
        f = f90.reindex(g.index).dropna()
        return {"n": int(len(g)), "up90": round(float((f > 0).mean() * 100)) if len(f) > 20 else None,
                "med90": round(float(f.median() * 100)) if len(f) > 20 else None,
                "dd90": round(float(g.dd90.median() * 100)) if len(g) > 20 else None,
                "dd90_p10": round(float(g.dd90.quantile(.1) * 100)) if len(g) > 20 else None}
    lv = {"0": st(bull[bull.k == 0]), "1": st(bull[bull.k == 1]), "2": st(bull[bull.k >= 2])}
    each = {e: st(bull[bull[e]]) for e in E}
    tops = []
    for t in TOPS:
        r = D.loc[pd.Timestamp(t) - pd.Timedelta(days=30):pd.Timestamp(t)]
        if len(r):
            km = int(r.k.max())
            tops.append({"date": t, "px": round(float(px.loc[t])), "k": km, "hit": km >= 2})
    last = D.iloc[-1]
    def fmt(x):
        return "–" if x is None or not np.isfinite(x) else str(int(round(float(x))))
    cond = [
        {"key": "E1", "name": "Định giá cao", "desc": f"điểm định giá từ {EX_VALUE}", "ok": bool(last.E1),
         "now": f"điểm định giá {fmt(last.value)}"},
        {"key": "E2", "name": "Đám đông FOMO", "desc": f"Fear & Greed từ {EX_FNG}, hoặc funding hạng từ {EX_FUND}", "ok": bool(last.E2),
         "now": f"F&G {fmt(last.fng)}" + (f" · funding hạng {fmt(last.fund)}" if np.isfinite(last.fund) else "")},
        {"key": "E3", "name": "Cá mập phân phối", "desc": f"điểm Cá mập ≤ {EX_WHALE} khi giá còn trong {abs(int(EX_NEAR * 100))}% quanh đỉnh 1 năm", "ok": bool(last.E3),
         "now": f"điểm Cá mập {fmt(last.whale)} · giá cách đỉnh 1 năm {fmt(last.near * 100)}%"},
        {"key": "E4", "name": "Vĩ mô quay sang thắt chặt", "desc": f"điểm Vĩ mô ≤ {EX_MACRO}, hoặc giảm ≥ {abs(EX_MDROP)} điểm trong 60 ngày", "ok": bool(last.E4),
         "now": f"điểm Vĩ mô {fmt(last.macro)} · 60 ngày {int(round(last.mac60)):+d} điểm" if np.isfinite(last.mac60) else f"điểm Vĩ mô {fmt(last.macro)}"},
    ]
    for c in cond:
        c["each"] = each[c["key"]]
        c["now"] = c["now"].replace("-", "−")
    k = int(last.k)
    return {"active": last.ph in ("rec", "eu"), "k": k, "level": ["Giữ", "Để ý", "Chốt dần"][min(k, 2)], "cond": cond, "levels": lv,
            "base90": st(bull)["up90"], "since": str(bull.index[0].date()) if len(bull) else None, "n": int(len(bull)), "tops": tops}


def bucket(v: float) -> int:
    return min(4, max(0, int(v // 20)))


def run() -> dict:
    t0 = time.time()
    X, px = load()
    S, R = scores(X)
    S = S[S.index >= "2015-01-01"]
    last = S.dropna(how="all").index[-1]
    P = phases(X, px)
    base90 = round(float((fwd(px, 90)[px.index >= "2015-01-01"].dropna() > 0).mean() * 100))

    sc = {}
    for g in GROUPS:
        s = S[g].dropna()
        v = float(s.iloc[-1])
        d7 = float(s.iloc[-1] - s.iloc[-8]) if len(s) > 8 else None
        items = []
        for f, sgn in GROUPS[g].items():
            if f not in X:
                continue
            raw = X[f].ffill(limit=10).dropna()
            if raw.empty:
                continue
            r = R[f].dropna()
            name, fmt = ITEM[f]
            items.append({"key": f, "name": name, "val": vn(fmt(float(raw.iloc[-1]))), "rank": round(float(r.iloc[-1])) if len(r) else None,
                          "asof": str(raw.index[-1].date()), "tested": f in TESTED})
        mk = {}
        for lab, day in (("top21", "2021-11-10"), ("bot22", "2022-11-21"), ("top25", "2025-10-06")):
            try:
                mk[lab] = round(float(S[g].loc[day]))
            except (KeyError, ValueError, TypeError):
                mk[lab] = None
        sc[g] = {"name": NAMES[g], "score": round(v), "d7": round(d7) if d7 is not None else None, "asof": str(s.index[-1].date()),
                 "items": items, "marks": mk, "verify": bucket_stats(S[g].dropna(), px)}

    # Pha tâm lý
    ph = P["ph"].iloc[-1]
    seg_start = P.index[-1]
    for t in reversed(P.index):
        if P.at[t, "ph"] != ph:
            break
        seg_start = t
    pst = {}
    for k in ORDER:
        g = P[P.ph == k]
        f90, f180 = fwd(px, 90).reindex(g.index).dropna(), fwd(px, 180).reindex(g.index).dropna()
        pst[k] = {"n": int(len(g)), "up90": round(float((f90 > 0).mean() * 100)) if len(f90) > 20 else None,
                  "up180": round(float((f180 > 0).mean() * 100)) if len(f180) > 20 else None,
                  "med180": round(float(f180.median() * 100)) if len(f180) > 20 else None,
                  "worst90": round(float(f90.min() * 100)) if len(f90) > 20 else None}
    eps, cur, st = [], None, None
    for t, p in P.ph.items():
        if p != cur:
            if cur == "apa" and (prev - st).days >= 7:   # noqa: F821
                f = fwd(px, 180).get(st)
                eps.append({"from": str(st.date()), "to": str(prev.date()), "px": round(float(px[st])),   # noqa: F821
                            "f180": None if f is None or not np.isfinite(f) else round(float(f * 100))})
            cur, st = p, t
        prev = t
    if cur == "apa":
        eps.append({"from": str(st.date()), "to": None, "px": round(float(px[st])), "f180": None})
    lr = P.iloc[-1]
    cond = [{"key": "cheap", "name": "Giá vẫn rẻ", "desc": f"giá ÷ trung bình 200 tuần dưới {CHEAP:.2f}".replace(".", ","),
             "now": round(float(lr.w), 2), "thr": CHEAP, "ok": bool(lr.w < CHEAP), "max": 2.0},
            {"key": "calm", "name": "Biến động dịu xuống", "desc": f"hạng biến động 30 ngày (so với 2 năm) dưới {VOL_PANIC}",
             "now": round(float(lr.volr)), "thr": VOL_PANIC, "ok": bool(lr.volr < VOL_PANIC), "max": 100},
            {"key": "fear", "name": "Hết cực sợ", "desc": f"Fear & Greed (trung bình 7 ngày) từ {FNG_PANIC} trở lên",
             "now": round(float(lr.fng)), "thr": FNG_PANIC, "ok": bool(lr.fng >= FNG_PANIC), "max": 100}]
    phase = {"key": ph, "name": PHASES[ph][0], "desc": PHASES[ph][1], "since": str(seg_start.date()), "asof": str(P.index[-1].date()),
             "stats": pst, "episodes": eps[-8:], "cond": cond, "names": {k: list(v) for k, v in PHASES.items()}, "order": ORDER}

    # Kết luận theo tầm nhìn
    m = sc["macro"]["score"]
    vb = sc["macro"]["verify"][bucket(m)]
    st_short = [("Nghiêng giảm / đi ngang", "bad"), ("Hơi xấu, dễ đi ngang", "warn"), ("Trung tính", "neutral"),
                ("Nghiêng tăng", "good"), ("Thuận lợi", "good")][bucket(m)]
    mword = ["điều kiện tài chính rất thắt chặt", "điều kiện tài chính thắt chặt", "điều kiện tài chính trung tính",
             "điều kiện tài chính nới lỏng", "điều kiện tài chính rất nới lỏng"][bucket(m)]
    pword = {"pan": "thị trường còn hoảng loạn", "apa": "thị trường đang chán nản (vùng gom)", "rec": "thị trường đang hồi phục",
             "eu": "thị trường hưng phấn", "wk": "thị trường suy yếu sau đỉnh"}[ph]
    w = float(lr.w)
    vv = sc["value"]["score"]
    vb2 = sc["value"]["verify"][bucket(vv)]
    zones = [(0, 1.0, "Nghiêng tăng", "good", "giá dưới trung bình 200 tuần — rẻ so với lịch sử"),
             (1.0, 1.5, "Nghiêng tăng nhẹ", "good", "giá gần vùng giá vốn dài hạn"),
             (1.5, 2.5, "Trung tính", "neutral", "giá đã cao hơn giá vốn dài hạn"),
             (2.5, 99, "Rủi ro cao", "bad", "giá đắt so với lịch sử, thường gần đỉnh chu kỳ")]
    z = next(z for z in zones if z[0] <= w < z[1])
    wr = np.exp(X["w200"]).reindex(px.index)
    f365 = fwd(px, 365)
    zm = (wr >= z[0]) & (wr < z[1]) & (px.index >= "2014-01-01")
    zf = f365[zm].dropna()
    mv = next((i["val"] for i in sc["value"]["items"] if i["key"] == "mvrv"), None)
    horizons = {
        "short": {"title": "Xu hướng 1–3 tháng tới", "label": st_short[0], "tone": st_short[1],
                  "why": f"{mword[0].upper() + mword[1:]}, {pword}.",
                  "stat": f"Vĩ mô ở mức {vb['lo']}–{vb['hi']}: {vb['up90']}% số lần giá cao hơn sau 3 tháng (bình thường {base90}%)." if vb.get("up90") is not None else ""},
        "long": {"title": "Xu hướng 1–2 năm tới", "label": z[2], "tone": z[3],
                 "why": f"{z[4][0].upper() + z[4][1:]}" + (f"; MVRV {mv}." if mv else "."),
                 "ratio": vn(f"{w:.2f}"),
                 "stat": f"Ở vùng giá này từ 2014: {round(float((zf > 0).mean() * 100))}% số lần giá cao hơn sau 1 năm." if len(zf) > 60 else ""},
    }
    signals = [
        {"key": "macro", "name": f"Vĩ mô vượt {SIG_MACRO}", "now": f"hiện {sc['macro']['score']}", "ok": sc["macro"]["score"] > SIG_MACRO},
        {"key": "whale", "name": f"Cá mập vượt {SIG_WHALE} (tích luỹ)", "now": f"hiện {sc['whale']['score']}", "ok": sc["whale"]["score"] > SIG_WHALE},
        {"key": "phase", "name": "Tâm lý sang \"Chán nản\"", "now": f"hiện {PHASES[ph][0]}", "ok": ph in ("apa", "rec")},
    ]

    ex = exits(S, P, X, px)

    # Chuỗi lịch sử cho phần "Xem chi tiết"
    H = S[S.index >= "2021-01-01"].join(px.rename("px"))
    hist = {"t": [d.strftime("%Y-%m-%d") for d in H.index], "px": [round(float(v)) if np.isfinite(v) else None for v in H.px]}
    for g in GROUPS:
        hist[g] = [round(float(v), 1) if np.isfinite(v) else None for v in H[g]]
    phist = {"t": [d.strftime("%Y-%m-%d") for d in P.index], "ph": list(P.ph), "px": [round(float(v)) for v in P.px.ffill()]}

    out = {"v": V, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "asof": str(last.date()),
           "price": round(float(px.iloc[-1])), "price_date": str(px.index[-1].date()), "base90": base90,
           "scores": sc, "phase": phase, "horizons": horizons, "signals": signals, "n_ok": sum(s["ok"] for s in signals), "exit": ex,
           "hist": hist, "phase_hist": phist, "runtime_sec": round(time.time() - t0, 1)}

    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(v) for v in o]
        if isinstance(o, (np.floating, np.integer)):
            o = float(o)
        if isinstance(o, float) and not np.isfinite(o):
            return None
        if isinstance(o, np.bool_):
            return bool(o)
        return o
    out = clean(out)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return out


if __name__ == "__main__":
    try:
        r = run()
        print(json.dumps({k: r[k] for k in ("asof", "horizons", "signals")}, ensure_ascii=False)[:1500])
        print({g: r["scores"][g]["score"] for g in r["scores"]}, r["phase"]["name"], r["phase"]["since"], r["runtime_sec"])
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    sys.exit(0)
