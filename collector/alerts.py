"""Cảnh báo qua Telegram: bản tin buổi sáng, vùng tâm lý cực đoan, funding bất thường, sự kiện vĩ mô cấp 1, lỗi bộ thu thập.

Cần hai secret trên GitHub: TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID. Thiếu thì bỏ qua, không lỗi.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STATE_FILE = os.path.join(DATA, "alerts_state.json")
CAL_CACHE = os.path.join(DATA, "calendar_week.json")
VN = timezone(timedelta(hours=7))
MAX_MARKET_PER_DAY = 3
ORDER = ["xf", "f", "n", "g", "xg"]
BAND_NAME = {"xf": "Sợ hãi cực độ", "f": "gần Sợ hãi cực độ", "n": "Trung tính", "g": "gần Tham lam cực độ", "xg": "Tham lam cực độ"}
TIER1 = [
    (r"^Core CPI", "CPI lõi"), (r"^CPI", "CPI (lạm phát)"), (r"Non-Farm Employment", "Bảng lương phi nông nghiệp (NFP)"),
    (r"Unemployment Rate", "Tỷ lệ thất nghiệp"), (r"Federal Funds Rate", "Fed công bố lãi suất"), (r"FOMC Statement", "Tuyên bố của Fed"),
    (r"FOMC Press Conference", "Họp báo của Fed"), (r"Core PCE", "PCE lõi"), (r"Fed Chair .*Speaks|Powell", "Chủ tịch Fed phát biểu"),
    (r"Advance GDP", "GDP sơ bộ"),
]


def vn(x: float | None, d: int = 0, sign: bool = False) -> str:
    if x is None:
        return "–"
    s = f"{x:+,.{d}f}" if sign else f"{x:,.{d}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def page_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def classify(v: float) -> str:
    return "xf" if v < 20 else "f" if v <= 25 else "xg" if v >= 80 else "g" if v >= 75 else "n"


def ci_band(v: float, prev: str | None) -> str:
    """Có độ trễ 3 điểm khi quay về trung tính, để không báo đi báo lại khi chỉ số dao động quanh ngưỡng."""
    b = classify(v)
    if prev is None or prev == b:
        return b
    pi, bi = ORDER.index(prev), ORDER.index(b)
    if pi < 2 and bi > pi:                     # phía sợ hãi, đang dịu đi
        nb = classify(v - 3)
        return nb if ORDER.index(nb) > pi else prev
    if pi > 2 and bi < pi:                     # phía tham lam, đang nguội đi
        nb = classify(v + 3)
        return nb if ORDER.index(nb) < pi else prev
    return b


def fund_band(p: float, prev: str | None) -> str:
    if p >= 95 or (prev == "hi" and p >= 90):
        return "hi"
    if p <= 5 or (prev == "lo" and p <= 10):
        return "lo"
    return "n"


def calendar() -> list:
    """Sự kiện Mỹ mức cao tuần này (ForexFactory), lưu bản gần nhất phòng khi nguồn lỗi."""
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        ev = r.json()
        with open(CAL_CACHE, "w", encoding="utf-8") as f:
            json.dump(ev, f)
    except Exception:  # noqa: BLE001
        try:
            with open(CAL_CACHE, encoding="utf-8") as f:
                ev = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
    out = []
    for e in ev:
        if e.get("country") != "USD" or e.get("impact") != "High":
            continue
        vi = next((v for p, v in TIER1 if re.search(p, e.get("title", ""), re.I)), None)
        if not vi:
            continue
        try:
            t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({"t": t, "vi": vi, "en": e.get("title", ""), "forecast": e.get("forecast") or "", "previous": e.get("previous") or ""})
    return sorted(out, key=lambda x: x["t"])


def send(token: str, chat: str, text: str) -> None:
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
    if r.status_code != 200:
        # Không in phản hồi: nhật ký của kho công khai ai cũng đọc được.
        raise RuntimeError(f"Telegram trả lỗi {r.status_code}")


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def composite_rows() -> list:
    p = os.path.join(DATA, "composite.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        rows = []
        for r in csv.DictReader(f):
            try:
                rows.append((datetime.fromisoformat(r["time_utc"].replace("Z", "+00:00")).timestamp(), float(r["value"])))
            except (ValueError, KeyError, TypeError):
                continue
        return rows


def musd(v: float) -> str:
    return f"{vn(v / 1e9, 2)} tỷ $" if v >= 1e9 else f"{vn(v / 1e6, 1)} triệu $"


def whale_line(M: dict) -> str | None:
    """Một dòng về cá voi Hyperliquid: tổng long/short và cụm giá thanh lý gần nhất (trong ±10%)."""
    W = (M.get("hl_whales") or {}).get("BTC")
    if not W:
        return None
    t = f"Cá voi Hyperliquid (BTC, {W['n_acc']} ví lớn): long {musd(W['long_usd'])}, short {musd(W['short_usd'])}"
    parts = []
    if W.get("near_long") and W["near_long"]["usd"] >= 5e6:
        c = W["near_long"]
        parts.append(f"{musd(c['usd'])} long bị thanh lý nếu giá xuống {vn(c['from'])}–{vn(c['to'])}")
    if W.get("near_short") and W["near_short"]["usd"] >= 5e6:
        c = W["near_short"]
        parts.append(f"{musd(c['usd'])} short bị thanh lý nếu giá lên {vn(c['from'])}–{vn(c['to'])}")
    return t + (". Cụm gần nhất: " + "; ".join(parts) if parts else "")


def mflow(m: float) -> str:
    """Tiền ròng (đơn vị triệu $) có dấu: +25,3 triệu $ / −1,07 tỷ $."""
    t = f"{vn(abs(m) / 1e3, 2)} tỷ $" if abs(m) >= 1e3 else f"{vn(abs(m), 0 if abs(m) >= 100 else 1)} triệu $"
    return ("+" if m > 0 else "−" if m < 0 else "") + t


def etf_line(latest: dict) -> str | None:
    E = (latest.get("etf") or {}).get("summary")
    if not E:
        return None
    d = datetime.strptime(E["date"], "%Y-%m-%d")
    st = E["streak"]
    t = f"ETF BTC phiên {d:%d/%m}: {mflow(E['flow'])}"
    if st["n"] >= 2:
        t += f", {'rút' if st['sign'] < 0 else 'vào'} {st['n']} phiên liền (tổng {mflow(st['sum'])})"
    return t + f"; 7 ngày {mflow(E['d7'])}, 30 ngày {mflow(E['d30'])}"


def compass_line(latest: dict) -> str | None:
    C = (latest.get("history_test") or {}).get("compass") or {}
    hd = C.get("head")
    if not hd:
        return None
    d = hd.get("d7")
    t = f"La bàn thị trường: {hd['band']} {hd['score']}/100" + (f" ({'▲' if d > 0 else '▼'}{abs(d)} so với 7 ngày trước)" if d else "")
    main = sorted((x for x in C.get("lenses", []) if x.get("main") and x.get("score") is not None), key=lambda x: x["score"])
    if main:
        lo, hi = main[0], main[-1]
        t += f"; yếu nhất {lo['name'].lower()} {lo['score']} ({lo['band'].lower()}), khá nhất {hi['name'].lower()} {hi['score']}"
    return t


def summary_lines(latest: dict) -> list:
    M = latest.get("market") or {}
    a, nw, so = latest.get("all") or {}, latest.get("news") or {}, latest.get("social") or {}
    L = []
    if a.get("index") is not None:
        L.append(f"• Văn bản: {a['index']}/100 (tin tức {nw.get('index', '–')}, mạng xã hội {so.get('index', '–')})")
    if M.get("fng"):
        L.append(f"• Fear &amp; Greed: {M['fng']['value']} ({html.escape(M['fng']['cls'])})")
    if M.get("funding"):
        L.append(f"• Funding BTC: phân vị {vn(M['funding']['pct'])}% trong 90 ngày ({M['funding']['src']})")
    if M.get("ls"):
        L.append(f"• Tài khoản đang long: {vn(M['ls']['buy'] * 100)}% ({M['ls']['src']})")
    if M.get("premium"):
        L.append(f"• Coinbase Premium 24h: {vn(M['premium']['avg24'], 3, True)}%")
    if M.get("stable") and M["stable"].get("ch30") is not None:
        L.append(f"• Stablecoin 30 ngày: {vn(M['stable']['ch30'], 1, True)}% ({vn(M['stable']['total'])} tỷ $)")
    dv = ((M.get("drivers") or {}).get("windows") or {}).get("24")
    if dv:
        an = dv.get("analysis")
        L.append(f"• Đà giá 24h: {an['title']}. {an['assess']}" if an else f"• Đà giá 24h: {dv['label']} (giá {vn(dv['dp'], 1, True)}%, open interest {vn(dv['doi'], 1, True)}%)")
    wl = whale_line(M)
    if wl:
        L.append("• " + wl)
    el = etf_line(latest)
    if el:
        L.append("• " + el)
    cl = compass_line(latest)
    if cl:
        L.append("• " + cl)
    mac = latest.get("macro") or {}
    if mac.get("label") and mac.get("have", 1):
        L.append(f"• Vĩ mô: {mac['label']} (điểm {mac.get('score', 0):+d})")
    if M.get("btc"):
        L.append(f"• BTC: {vn(M['btc']['price'])} $" + (f" (24h {vn(M['btc']['ch24'], 1, True)}%)" if M["btc"].get("ch24") is not None else ""))
    return L


def load_ctx() -> dict:
    """Bối cảnh thị trường do collector/context.py tính mỗi sáng (data/context.json)."""
    try:
        C = json.load(open(os.path.join(os.path.dirname(STATE_FILE), "context.json"), encoding="utf-8"))
        return C if C.get("scores") else {}
    except (OSError, ValueError):
        return {}


CTX_STATE = {
    "macro": ["rất thắt chặt", "thắt chặt", "trung tính", "nới lỏng", "rất nới lỏng"],
    "whale": ["phân phối mạnh", "phân phối", "trung tính", "tích luỹ", "tích luỹ mạnh"],
    "value": ["định giá thấp", "hơi thấp", "hợp lý", "hơi cao", "định giá cao"],
}


def ctx_lines(C: dict) -> list:
    if not C:
        return []
    b = lambda v: min(4, max(0, int(v // 20)))  # noqa: E731
    H, S, P = C["horizons"], C["scores"], C["phase"]
    L = [f"• 1–3 tháng tới: <b>{html.escape(H['short']['label'])}</b> ({html.escape(H['short']['why'].rstrip('.').lower())})",
         f"• 1–2 năm tới: <b>{html.escape(H['long']['label'])}</b>",
         f"• Vĩ mô {S['macro']['score']}/100 ({CTX_STATE['macro'][b(S['macro']['score'])]}) · Cá mập {S['whale']['score']} ({CTX_STATE['whale'][b(S['whale']['score'])]})"
         f" · Định giá {S['value']['score']} ({CTX_STATE['value'][b(S['value']['score'])]})",
         f"• Tâm lý đám đông: pha <b>{html.escape(P['name'])}</b>",
         f"• Dấu hiệu xu hướng đổi chiều đi lên: <b>{C['n_ok']}/3</b> (" + ", ".join(("✅ " if x["ok"] else "▫️ ") + html.escape(x["name"]) for x in C["signals"]) + ")"]
    return L


ZONES = [(20, "Sợ hãi cực độ"), (40, "Sợ hãi"), (60, "Trung tính"), (80, "Tham lam"), (101, "Tham lam cực độ")]


def zone(v: float) -> str:
    return next(n for lim, n in ZONES if v < lim)


def run(latest: dict) -> dict:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    st = load_state()
    now = datetime.now(timezone.utc)
    vnow = now.astimezone(VN)
    vday = vnow.strftime("%Y-%m-%d")
    if st.get("day") != vday:
        st["day"], st["market_count"] = vday, 0
    st.setdefault("sent", {})
    st.setdefault("events", [])
    msgs: list[tuple[str, str]] = []     # (loại, nội dung)
    M = latest.get("market") or {}
    ci = (M.get("composite") or {}).get("value")
    url = page_url()
    rows = composite_rows()
    st.setdefault("bands", {})

    def recently(key: str, hours: float) -> bool:
        return time.time() - st["sent"].get(key, 0) < hours * 3600

    # 1) vùng cực đoan của chỉ số tâm lý tổng hợp cũ: đã tắt (trang Tổng quan nay dùng Bối cảnh thị trường)
    if False and ci is not None:
        prev = st["bands"].get("ci")
        b = ci_band(ci, prev)
        st["bands"]["ci"] = b
        if prev and b != prev:
            pi, bi = ORDER.index(prev), ORDER.index(b)
            if abs(bi - 2) > abs(pi - 2):
                head = f"⚠️ Tâm lý vào vùng <b>{BAND_NAME[b]}</b>: {ci}/100"
                tip = ("Vùng này thường gần các đáy ngắn hạn. Trader tâm lý chờ giá ngừng giảm rồi mới tính, không bắt dao rơi."
                       if b in ("xf", "f") else "Vùng này thường gần các đỉnh ngắn hạn. Trader tâm lý thường giảm đòn bẩy và chốt bớt.")
            else:
                head = f"ℹ️ Tâm lý rời vùng {BAND_NAME[prev]}, nay {ci}/100 ({zone(ci)})"
                tip = "Mức cực đoan đã dịu. Theo dõi xem giá có xác nhận hướng mới không."
            msgs.append(("market", f"{head}\n\n" + "\n".join(summary_lines(latest)) + f"\n\n{tip}"))

    # 2) thay đổi mạnh trong 24 giờ
    if False and ci is not None and rows:      # đã tắt cùng chỉ số tổng hợp cũ
        ref = [v for t, v in rows if t <= time.time() - 23 * 3600]
        if ref and abs(ci - ref[-1]) >= 15 and not recently("move", 12):
            d = ci - ref[-1]
            msgs.append(("market:move", f"{'📈' if d > 0 else '📉'} Tâm lý {'tăng' if d > 0 else 'giảm'} mạnh: {ref[-1]:.0f} → {ci} trong 24 giờ\n\n" + "\n".join(summary_lines(latest))))

    # 2b) giá BTC chạy mạnh: nói rõ đà đến từ đâu
    dv = ((M.get("drivers") or {}).get("windows") or {}).get("24")
    if dv and abs(dv["dp"]) >= 5 and not recently("drivers", 12):
        liq = (M.get("drivers") or {}).get("liq") or {}
        extra = f"\nThanh lý trên OKX ({liq.get('hours', 24):.0f} giờ): short {vn(liq.get('short', 0) / 1e6, 1)} triệu $, long {vn(liq.get('long', 0) / 1e6, 1)} triệu $." if liq else ""
        an = dv.get("analysis")
        if an:
            body = (f"{'🚀' if dv['dp'] > 0 else '🔻'} <b>{html.escape(an['title'])}</b> (24 giờ)\n\n{html.escape(an['verdict'])}\n\n<b>Kết luận:</b> {html.escape(an['assess'])}"
                    + ("\n\n<b>Theo dõi:</b>\n" + "\n".join("• " + html.escape(x) for x in an["watch"]) if an.get("watch") else ""))
            msgs.append(("market:drivers", body))
        else:
            msgs.append(("market:drivers", f"{'🚀' if dv['dp'] > 0 else '🔻'} BTC {vn(dv['dp'], 1, True)}% trong 24 giờ: <b>{html.escape(dv['label'])}</b>\n\n{html.escape(dv['why'])}\n"
                         f"Open interest {vn(dv['doi'], 1, True)}% · mua spot ròng {vn(dv['spot_btc'], 0, True) if dv.get('spot_btc') is not None else '–'} BTC (OKX) · Coinbase Premium {vn(dv['prem'], 3, True) if dv.get('prem') is not None else '–'}%{extra}"))

    # 2c) thanh lý dây chuyền: một giờ có tổng thanh lý BTC (7 sàn) vượt phân vị 99% của 30 ngày
    LA = M.get("liq_agg") or {}
    thr = max(LA["hour_p99"], 20e6) if LA.get("hour_p99") else 40e6
    cand = [x for x in (LA.get("series") or []) if x[0] / 1000 >= time.time() - 3 * 3600 and x[1] + x[2] >= thr]
    if cand and not recently("liq", 4):
        b = max(cand, key=lambda x: x[1] + x[2])
        if st.get("liq_bucket") != b[0]:
            side = "long" if b[1] >= b[2] else "short"
            v, other = (b[1], b[2]) if side == "long" else (b[2], b[1])
            t0 = datetime.fromtimestamp(b[0] / 1000, VN)
            pr = {p[0]: p[1] for p in ((M.get("drivers") or {}).get("series") or {}).get("price", [])}
            p0, p1 = pr.get(b[0]), pr.get(b[0] + 3600000) or (list(pr.values())[-1] if pr else None)
            mv = f" Giá BTC trong giờ đó: {vn((p1 / p0 - 1) * 100, 1, True)}% ({vn(p0)} → {vn(p1)})." if p0 and p1 else ""
            body = (f"💥 <b>Thanh lý dây chuyền BTC</b>: {side} bị thanh lý {musd(v)} trong giờ {t0:%H}:00–{(t0 + timedelta(hours=1)):%H}:00"
                    f" (phe kia {musd(other)}), gấp {vn((b[1] + b[2]) / LA['hour_p95'], 1) if LA.get('hour_p95') else '–'} lần một giờ sôi động thường gặp.{mv}\n\n"
                    + ("Long bị thanh lý dồn dập: giá bị đạp xuống bằng lệnh bán cưỡng bức. " if side == "long" else "Short bị thanh lý dồn dập: giá bị đẩy lên bằng lệnh mua cưỡng bức (short squeeze). "))
            hx = ((latest.get("history_test") or {}).get("liq_ext") or {}).get(side)
            if hx:
                body += (f"Lịch sử từ 2022 ({hx['n']} ngày {side} bị thanh lý cực lớn): BTC 7 ngày sau tăng ở {hx['up7']}% số lần, trung vị {vn(hx['med7'], 1, True)}%"
                         f" (mọi ngày khoảng 52%).")
            W = (M.get("hl_whales") or {}).get("BTC") or {}
            c = W.get("near_long" if side == "long" else "near_short")
            if c and c["usd"] >= 5e6:
                body += f"\nCá voi Hyperliquid: còn {musd(c['usd'])} {side} sẽ bị thanh lý nếu giá {'xuống' if side == 'long' else 'lên'} {vn(c['from'])}–{vn(c['to'])}."
            msgs.append(("market:liq", body))
            st["liq_bucket"] = b[0]

    # 2d) dòng tiền ETF BTC: phiên mới có rút/vào rất mạnh, hoặc chuỗi cùng chiều đạt 5 phiên
    EF = latest.get("etf") or {}
    E, EH = EF.get("summary"), EF.get("hist") or {}
    if E and st.get("etf_day") != E["date"]:
        first = "etf_day" not in st
        st["etf_day"] = E["date"]
        big = EH.get("big_thr") is not None and E["flow"] <= EH["big_thr"]
        streak5 = E["streak"]["n"] == 5
        if not first and (big or streak5):
            out = E["flow"] < 0 if big else E["streak"]["sign"] < 0
            body = f"{'🏦🔻' if out else '🏦🟢'} <b>Dòng tiền ETF BTC</b>: {etf_line(latest)}."
            if E.get("top"):
                body += "\nĐóng góp lớn nhất: " + ", ".join(f"{k} {mflow(v)}" for k, v in E["top"]) + "."
            hx = EH.get("big_out" if big else ("out" if out else "in"))
            if hx and hx.get("d7"):
                what = f"phiên rút từ {mflow(EH['big_thr'])}" if big else f"chuỗi {EH.get('streak_n', 4)} phiên {'rút' if out else 'vào'} liền"
                body += (f"\n\nLịch sử từ 2024 ({hx['d7']['n']} lần có {what}): BTC 7 ngày sau tăng ở {hx['d7']['up']}% số lần, trung vị {vn(hx['d7']['med'], 1, True)}%"
                         + (f"; 30 ngày sau tăng {hx['d30']['up']}%, trung vị {vn(hx['d30']['med'], 1, True)}%" if hx.get("d30") else "")
                         + (f" (mọi ngày: 7 ngày tăng {EH['base']['d7']['up']}%)." if EH.get("base") else "."))
            msgs.append(("market:etf", body))

    # 3) funding bất thường
    fd = M.get("funding")
    if False and fd:                           # đã tắt: cảnh báo lẻ, funding nằm trong tab Cá mập & phái sinh
        prev = st["bands"].get("funding")
        b = fund_band(fd["pct"], prev)
        st["bands"]["funding"] = b
        if prev and b != prev and b != "n":
            txt = ("Phe long đang rất đông và trả phí cao. Nếu giá giảm, các lệnh long dễ bị thanh lý dây chuyền."
                   if b == "hi" else "Phe short đang rất đông. Nếu giá tăng, short có thể bị ép mua lại (short squeeze).")
            msgs.append(("market:funding", f"⚠️ Funding BTC ở phân vị <b>{vn(fd['pct'])}%</b> của 90 ngày ({vn(fd['last'] * 100, 4, True)}% mỗi 8 giờ, {fd['src']})\n\n{txt}"))

    # 3b) Bối cảnh thị trường: một dấu hiệu đổi chiều bật/tắt, hoặc chuyển pha tâm lý (số tính mỗi sáng)
    C = load_ctx()
    if C:
        old = st.get("ctx") or {}
        now_sig = {x["key"]: bool(x["ok"]) for x in C["signals"]}
        now_ph = C["phase"]["key"]
        if old and old.get("asof") != C["asof"]:
            ch = []
            for x in C["signals"]:
                if x["key"] in old.get("sig", {}) and old["sig"][x["key"]] != now_sig[x["key"]]:
                    ch.append(f"{'✅ Bật' if now_sig[x['key']] else '↩️ Tắt'}: <b>{html.escape(x['name'])}</b> ({html.escape(x['now'])})")
            if old.get("phase") and old["phase"] != now_ph:
                pn = C["phase"]["names"]
                ch.append(f"🔄 Tâm lý đám đông chuyển pha: {html.escape(pn[old['phase']][0])} → <b>{html.escape(pn[now_ph][0])}</b>. {html.escape(pn[now_ph][1])}")
            if ch:
                msgs.append(("ctx", "🧭 <b>Bối cảnh thị trường thay đổi</b>\n" + "\n".join(ch) + "\n\n" + "\n".join(ctx_lines(C))))
        st["ctx"] = {"asof": C["asof"], "sig": now_sig, "phase": now_ph}

    # 4) sự kiện vĩ mô cấp 1 trong 3 giờ tới
    try:
        cal = calendar()
    except Exception:  # noqa: BLE001
        cal = []
    soon = [e for e in cal if timedelta(0) < e["t"] - now <= timedelta(hours=3)]
    new_ev = [e for e in soon if f"{e['en']}|{e['t'].isoformat()}" not in st["events"]]
    if new_ev:
        lines = []
        for e in new_ev:
            st["events"].append(f"{e['en']}|{e['t'].isoformat()}")
            mins = int((e["t"] - now).total_seconds() // 60)
            extra = " · ".join(x for x in (f"dự báo {e['forecast']}" if e["forecast"] else "", f"kỳ trước {e['previous']}" if e["previous"] else "") if x)
            lines.append(f"• <b>{e['vi']}</b> lúc {e['t'].astimezone(VN):%H:%M} (còn {" ".join(x for x in (f"{mins // 60} giờ" if mins >= 60 else "", f"{mins % 60} phút" if mins % 60 or mins < 60 else "") if x)})" + (f"\n   {html.escape(extra)}" if extra else ""))
        msgs.append(("event", "🗓 Sắp có số liệu vĩ mô quan trọng của Mỹ:\n" + "\n".join(lines) + "\n\nBiến động BTC thường tăng mạnh vài phút quanh giờ công bố; cẩn thận với đòn bẩy và lệnh dừng lỗ quá sát."))
    st["events"] = st["events"][-60:]

    # 5) bộ thu thập thiếu dữ liệu
    issues = []
    if (latest.get("kept_count") or 0) < 40:
        issues.append(f"chỉ giữ được {latest.get('kept_count', 0)} bài")
    if ci is None or len((M.get("composite") or {}).get("parts", [])) < 4:
        issues.append(f"chỉ số tổng hợp chỉ có {len((M.get('composite') or {}).get('parts', []))} thành phần")
    mac = latest.get("macro") or {}
    if mac.get("error") or (mac.get("have") is not None and mac["have"] < 6):
        issues.append("thiếu nhiều số liệu vĩ mô")
    if issues and not recently("health", 24):
        msgs.append(("health", "🔧 Bộ thu thập đang thiếu dữ liệu: " + "; ".join(issues) + ". Trang vẫn chạy nhưng kém đầy đủ. Nhắn Claude kèm ảnh chụp trang nếu kéo dài."))

    # 6) bản tin buổi sáng
    if vnow.hour >= 7 and st.get("brief_day") != vday:
        L = [f"☀️ <b>Bản tin thị trường {vnow:%d/%m}</b>"]
        if M.get("btc"):
            L.append(f"BTC {vn(M['btc']['price'])} $" + (f" (24h {vn(M['btc']['ch24'], 1, True)}%)" if M["btc"].get("ch24") is not None else ""))
        cl = ctx_lines(C)
        if cl:
            L.append("\n<b>Bối cảnh</b>" + (f" (số liệu đến {C['asof'][8:10]}/{C['asof'][5:7]})" if C.get("asof") else ""))
            L += cl
        el = etf_line(latest)
        if el:
            L.append("• " + el)
        today = [e for e in cal if e["t"].astimezone(VN).strftime("%Y-%m-%d") == vday and e["t"] > now]
        if today:
            L.append("\nSự kiện cấp 1 hôm nay: " + "; ".join(f"{e['vi']} lúc {e['t'].astimezone(VN):%H:%M}" for e in today))
        msgs.append(("brief", "\n".join(L)))

    if token and chat and not st.get("welcomed"):
        msgs.insert(0, ("welcome", "✅ Đã kết nối cảnh báo của Sentiment Analysis.\nBạn sẽ nhận: bản tin mỗi sáng (khoảng 7–8 giờ), báo khi bối cảnh thị trường đổi (dấu hiệu đổi chiều bật/tắt, chuyển pha tâm lý), thanh lý dây chuyền, dòng tiền ETF bất thường, và nhắc trước các số liệu vĩ mô lớn của Mỹ."))

    status = {"configured": bool(token and chat), "sent": [], "skipped": []}
    for kind, text in msgs:
        key = kind.split(":")[-1]
        if kind.startswith("market"):
            if st["market_count"] >= MAX_MARKET_PER_DAY:
                status["skipped"].append(key)
                continue
        if url and kind != "welcome":
            text += f"\n\n<a href=\"{url}\">Mở trang</a>"
        if not (token and chat):
            status["skipped"].append(key)
            continue
        try:
            send(token, chat, text)
            status["sent"].append(key)
            st["sent"][key] = time.time()
            if kind.startswith("market"):
                st["market_count"] += 1
            if kind == "brief":
                st["brief_day"] = vday
            if kind == "welcome":
                st["welcomed"] = True
            time.sleep(1)
        except Exception as ex:  # noqa: BLE001
            status["error"] = str(ex)[:100]
    if not (token and chat) and any(k == "brief" for k, _ in msgs):
        st["brief_day"] = vday   # chưa cài Telegram thì thôi, không dồn bản tin
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    print("Cảnh báo:", status)
    return status
