"""Bộ thu thập tâm lý crypto (tầng 1–4), chạy mỗi giờ trên GitHub Actions.

Tầng 1: đọc tin tức (RSS), Reddit, Mastodon, Lemmy, Hacker News.
Tầng 2: lọc trùng, bot, quảng cáo, bài không phải tiếng Anh hoặc không về crypto.
Tầng 3: chấm điểm bằng từ điển thuật ngữ crypto + mô hình Twitter RoBERTa (ONNX).
Tầng 4: tổng hợp chỉ số, lưu data/latest.json và lịch sử data/history.csv.
"""
from __future__ import annotations

import calendar
import csv
import html
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import feedparser
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ITEMS_FILE = os.path.join(DATA, "items.jsonl")
LATEST_FILE = os.path.join(DATA, "latest.json")
HISTORY_FILE = os.path.join(DATA, "history.csv")
TRANS_FILE = os.path.join(DATA, "translations.json")
DAILY_CHAR_BUDGET = 4500  # MyMemory miễn phí: 5.000 ký tự/ngày khi không đăng ký
KEEP_DAYS = 7
VN = timezone(timedelta(hours=7))
UA = "Mozilla/5.0 (compatible; crypto-sentiment-collector/1.0; personal research)"
NOW = time.time()

RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "NewsBTC": "https://www.newsbtc.com/feed/",
    "CryptoPotato": "https://cryptopotato.com/feed/",
}
SUBREDDITS = ["CryptoCurrency", "Bitcoin", "ethereum", "CryptoMarkets", "BitcoinMarkets"]
MASTODON_TAGS = ["bitcoin", "btc", "crypto", "cryptocurrency", "ethereum"]
# Kênh YouTube crypto lớn (tiêu đề video phản ánh giọng điệu của người có ảnh hưởng). Tên thật lấy từ RSS.
YOUTUBE = {
    "UCqK_GSMbpiV8spgD3ZGloSw": "Coin Bureau", "UCbLhGKVY-bJPcawebgtNfbw": "Altcoin Daily", "UCRvqjQPSeaWn-uEx-w0XOIg": "Benjamin Cowen",
    "UCN9Nj4tjXbVTLYWN0EKly_Q": "Crypto Banter", "UCl2oCaw8hdR_kbqyqd2klIA": "Lark Davis", "UClgJyzwGs-GyaNxUHcLZrkg": "InvestAnswers",
    "UCCatR7nWbYrkVXdxXb4cGXw": "DataDash", "UCAl9Ld79qaZxp9JzEOwd3aA": "Bankless", "UCc4Rz_T9Sb1w5rqqo9pL1Og": "The Moon",
    "UCiUnrCUGCJTCC7KjuW493Ww": "Crypto Zombie", "UC4VPa7EOvObpyCRI4YKRQRw": "Paul Barron Network",
}
# Kênh Telegram công khai (đọc qua trang xem trước t.me/s/), chủ yếu là kênh tin nhanh.
TELEGRAM = ["WatcherGuru", "cointelegraph", "bitcoinmagazinetelegram", "wublockchainenglish"]

# ---------------------------------------------------------------- tầng 3a: từ điển
PHRASES = [("to the moon", 2), ("all-time high", 2), ("all time high", 2), ("new high", 1.5), ("record high", 1.6),
           ("buy the dip", 1.2), ("short squeeze", 1), ("long squeeze", -1), ("below support", -1.2), ("above resistance", 1.2),
           ("lost everything", -2.4), ("sell-off", -1.8), ("sell off", -1.8), ("rug pull", -2.2), ("hot cpi", -1),
           ("going up", 1), ("going down", -1), ("bull trap", -2.2), ("bear trap", 1.8), ("dead cat bounce", -1.8),
           ("short liquidations", 1.2), ("shorts liquidated", 1.2), ("shorts got liquidated", 1.2), ("long liquidations", -1.2),
           ("longs liquidated", -1.2), ("longs got liquidated", -1.2), ("we're so back", 2), ("so back", 1.5), ("it's so over", -2), ("so over", -1.5)]
WORDS = {
    "bullish": 2, "bull": 1.2, "bulls": 1.2, "moon": 1.8, "mooning": 2, "pump": 1.2, "pumping": 1.5, "pumped": 1.2, "rally": 1.5,
    "rallies": 1.5, "rallying": 1.5, "surge": 1.8, "surges": 1.8, "surging": 1.8, "soar": 1.8, "soars": 1.8, "soaring": 1.8,
    "breakout": 1.5, "ath": 2, "record": 0.8, "buy": 0.6, "buying": 0.9, "bought": 0.6, "accumulate": 1.2, "accumulating": 1.2,
    "accumulation": 1.2, "hodl": 1, "hodling": 1, "wagmi": 1.5, "lfg": 1.5, "gain": 1, "gains": 1.2, "profit": 1.2, "profits": 1.2,
    "green": 0.6, "recover": 1, "recovers": 1.1, "recovery": 1.2, "rebound": 1.4, "rebounds": 1.4, "inflow": 1.5, "inflows": 1.5,
    "adoption": 1.2, "approve": 1.2, "approves": 1.4, "approved": 1.5, "approval": 1.2, "upgrade": 0.8, "strong": 1, "stronger": 1,
    "strength": 1, "optimistic": 1.5, "confident": 1.2, "great": 1.2, "good": 0.7, "love": 1.1, "win": 1, "winning": 1.2,
    "success": 1.2, "successful": 1.2, "opportunity": 0.8, "undervalued": 1, "higher": 0.6, "rise": 1, "rises": 1, "rising": 1,
    "climb": 1, "climbs": 1.2, "jump": 1.2, "jumps": 1.2, "rocket": 1.5, "bullrun": 2, "outperform": 1.2, "outperforms": 1.2,
    "excited": 1.3, "amazing": 1.5, "best": 1, "huge": 0.5, "boost": 1.2, "boosts": 1.2,
    "bearish": -2, "bear": -1.2, "bears": -1.2, "dump": -1.8, "dumps": -1.8, "dumping": -1.8, "dumped": -1.6, "crash": -2,
    "crashes": -2, "crashing": -2, "crashed": -2, "plunge": -2, "plunges": -2, "plunged": -2, "slide": -1.2, "slides": -1.2,
    "sliding": -1.2, "drop": -1.2, "drops": -1.2, "dropped": -1.2, "fall": -1, "falls": -1, "falling": -1.2, "fell": -1.1,
    "sell": -0.6, "selling": -1, "sold": -0.6, "selloff": -1.8, "liquidated": -1.8, "liquidation": -1.5, "liquidations": -1.5,
    "rekt": -2, "ngmi": -1.5, "scam": -2, "scams": -2, "scammer": -2, "scammers": -2, "ponzi": -2.2, "fraud": -2, "rug": -1.8,
    "rugpull": -2.2, "hack": -2, "hacked": -2, "hacks": -2, "exploit": -1.8, "exploited": -1.8, "stolen": -1.8, "outflow": -1.5,
    "outflows": -1.5, "fear": -1.5, "fears": -1.5, "panic": -1.8, "capitulation": -1.8, "red": -0.5, "loss": -1.2,
    "losses": -1.4, "lost": -1.1, "lose": -1.1, "losing": -1.2, "worst": -1.8, "bad": -1, "terrible": -2, "awful": -2,
    "weak": -1, "weaker": -1, "weakness": -1.2, "risky": -0.8, "bubble": -1.4, "collapse": -2, "collapsed": -2, "collapses": -2,
    "bankrupt": -2.2, "bankruptcy": -2.2, "insolvent": -2, "lawsuit": -1.4, "sued": -1.4, "sues": -1.4, "ban": -1.5,
    "bans": -1.5, "banned": -1.5, "crackdown": -1.6, "decline": -1.2, "declines": -1.2, "declining": -1.2, "lower": -0.6,
    "tumble": -1.8, "tumbles": -1.8, "slump": -1.8, "slumps": -1.8, "worry": -1.2, "worries": -1.2, "worried": -1.4,
    "concern": -0.8, "concerns": -0.8, "uncertainty": -1, "overvalued": -1, "useless": -1.8, "waste": -1.4, "garbage": -2,
    "dead": -1.5, "dying": -1.5, "warning": -1, "warns": -1.2, "downturn": -1.5, "recession": -1.4, "inflation": -0.4,
    "delist": -1.6, "delisted": -1.6, "delisting": -1.6, "underperform": -1.2, "underperforms": -1.2, "struggling": -1.3,
    "struggles": -1.3, "hate": -1.5, "stupid": -1.5, "ridiculous": -1.2, "fail": -1.4, "fails": -1.4, "failed": -1.4,
    "failure": -1.6, "failing": -1.4, "greed": -0.3, "fud": -1,
}
EMOJI = [("🚀", 1.5), ("📈", 1.2), ("🟢", 0.8), ("💎", 0.8), ("🐂", 1.2), ("🔥", 0.5), ("📉", -1.2), ("🔴", -0.8),
         ("💀", -1.2), ("😱", -1.2), ("🩸", -1.4), ("🐻", -1.2), ("😭", -1)]
NEG = {"not", "no", "never", "isn't", "wasn't", "aren't", "don't", "doesn't", "didn't", "won't", "can't", "cannot", "hardly",
       "without", "nor", "isnt", "dont", "doesnt", "didnt", "wont", "cant"}


NOT_NEGATORS = re.compile(r"\b(no doubt|no wonder|not only|no matter|not just|nothing but|never been better|can't wait|cannot wait)\b")


def _neg_before(clause: str, pos: int) -> bool:
    """Có từ phủ định trong 4 từ đứng trước vị trí pos, cùng một vế câu (không vượt dấu phẩy, chấm)."""
    before = [b.replace("’", "'") for b in re.split(r"[^a-z'’-]+", clause[:pos]) if b][-4:]
    return any(b in NEG for b in before)


def lex_score(text: str) -> float:
    s = NOT_NEGATORS.sub(" ", (text or "").lower()[:700])
    s = re.sub(r"https?://\S+", " ", s)
    total = 0.0
    for e, w in EMOJI:
        if e in s:
            total += w
    used = set()
    for clause in re.split(r"[,.;:!?\n]+|\bbut\b", s):
        for p, w in PHRASES:
            i = clause.find(p)
            if i >= 0 and p not in used:
                used.add(p)
                total += -0.7 * w if _neg_before(clause, i) else w   # "never hits a new ATH" là tiêu cực
                clause = clause.replace(p, " ")
        toks = [t for t in re.split(r"[^a-z'’-]+", clause) if t]
        for i, tk in enumerate(toks):
            key = tk.replace("’", "'")
            w = WORDS.get(key)
            if not w or key in used:
                continue
            used.add(key)
            neg = any(toks[k].replace("’", "'") in NEG for k in range(max(0, i - 4), i))
            total += -0.7 * w if neg else w
    return math.tanh(total / 2.5)


# ---------------------------------------------------------------- tầng 1: thu thập
BOILER = re.compile(r"(the post .{0,200}? appeared first on .{0,80}?\.?$|continue reading.*$|read more.*$|\[…\]|\[\.\.\.\]"
                    r"|submitted by\s+/?u/[\w-]+|\[link\]|\[comments\]|&#32;)", re.I)


def strip(markup: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>", " ", markup or "", flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", html.unescape(t)).strip()
    return BOILER.sub("", t).strip()


def get_json(url: str, **kw):
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20, **kw)
    r.raise_for_status()
    return r.json()


def fetch_rss() -> tuple[list, dict]:
    items, status = [], {}
    for name, url in RSS_FEEDS.items():
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            n = 0
            for e in feed.entries[:60]:
                ts = e.get("published_parsed") or e.get("updated_parsed")
                t = calendar.timegm(ts) if ts else NOW
                summary = strip(e.get("summary", ""))[:400]
                items.append({"src": "Tin tức", "outlet": name, "id": "rss:" + (e.get("link") or e.get("id") or e.get("title", ""))[:200],
                              "t": t, "text": strip(e.get("title", "")) + ". " + summary, "title": strip(e.get("title", "")),
                              "url": e.get("link"), "author": name, "eng": 0, "lang": "en", "bot": False, "kind": "news"})
                n += 1
            status[name] = n
        except Exception as ex:  # noqa: BLE001
            status[name] = "lỗi: " + str(ex)[:80]
    return items, status


def fetch_reddit() -> tuple[list, dict]:
    """Reddit chặn API JSON từ máy chủ đám mây, nên đọc RSS và nghỉ giữa các lần gọi."""
    items, status = [], {}
    h = datetime.now(timezone.utc).hour
    order = SUBREDDITS[h % len(SUBREDDITS):] + SUBREDDITS[:h % len(SUBREDDITS)]   # xoay vòng để sub bị chặn không luôn là một
    for k, sub in enumerate(order):
        if k:
            time.sleep(8)
        for attempt in range(2):
            try:
                r = requests.get(f"https://www.reddit.com/r/{sub}/new/.rss?limit=50", headers={"User-Agent": UA}, timeout=20)
                if r.status_code == 429 and attempt == 0:
                    time.sleep(30)
                    continue
                r.raise_for_status()
                feed = feedparser.parse(r.content)
                for e in feed.entries:
                    ts = e.get("updated_parsed") or e.get("published_parsed")
                    items.append({"src": "Reddit", "outlet": "r/" + sub, "id": "rd:" + e.get("id", e.get("link", "")),
                                  "t": calendar.timegm(ts) if ts else NOW, "text": strip(e.get("title", "")) + ". " + strip(e.get("summary", ""))[:600],
                                  "title": strip(e.get("title", "")), "url": e.get("link"), "author": (e.get("author") or ""),
                                  "eng": 0, "lang": "en", "bot": False, "kind": "social"})
                status["r/" + sub] = len(feed.entries)
                break
            except Exception as ex:  # noqa: BLE001
                status["r/" + sub] = "lỗi: " + str(ex)[:80]
                break
    return items, status


def fetch_mastodon() -> tuple[list, dict]:
    items, status = [], {}
    for tag in MASTODON_TAGS:
        try:
            out, max_id = [], None
            for _ in range(3):
                a = get_json(f"https://mastodon.social/api/v1/timelines/tag/{tag}?limit=40" + (f"&max_id={max_id}" if max_id else ""))
                if not a:
                    break
                out += a
                max_id = a[-1]["id"]
            for x in out:
                acct = x.get("account") or {}
                items.append({"src": "Mastodon", "outlet": "#" + tag, "id": "m:" + x["id"], "t": datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")).timestamp(),
                              "text": strip(x.get("content")), "title": None, "url": x.get("url"), "author": acct.get("acct"),
                              "eng": (x.get("favourites_count") or 0) + (x.get("reblogs_count") or 0) + (x.get("replies_count") or 0),
                              "lang": x.get("language"), "bot": bool(acct.get("bot")), "kind": "social"})
            status["#" + tag] = len(out)
        except Exception as ex:  # noqa: BLE001
            status["#" + tag] = "lỗi: " + str(ex)[:80]
    return items, status


def fetch_youtube() -> tuple[list, dict]:
    items, status = [], {}
    for cid, name in YOUTUBE.items():
        try:
            r = requests.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": cid}, headers={"User-Agent": UA}, timeout=15)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            ch = (feed.feed.get("title") or name).strip()
            for e in feed.entries:
                ts = e.get("published_parsed") or e.get("updated_parsed")
                title = strip(e.get("title", ""))   # chỉ dùng tiêu đề: phần mô tả thường là quảng cáo, mã giới thiệu
                items.append({"src": "YouTube", "outlet": ch, "id": "yt:" + e.get("yt_videoid", e.get("id", "")), "t": calendar.timegm(ts) if ts else NOW,
                              "text": title, "title": title, "url": e.get("link"), "author": ch,
                              "eng": 0, "lang": "en", "bot": False, "kind": "social"})
            status["YT " + ch] = len(feed.entries)
        except Exception as ex:  # noqa: BLE001
            status["YT " + name] = "lỗi: " + str(ex)[:80]
        time.sleep(0.5)
    return items, status


def fetch_stocktwits() -> tuple[list, dict]:
    """Stocktwits: mạng xã hội của trader; người đăng tự gắn nhãn Bullish/Bearish (đo tâm lý trực tiếp, không cần AI đoán)."""
    items, status = [], {}
    for sym in ("BTC.X", "ETH.X"):
        try:
            out, max_id = [], None
            for _ in range(3):
                params = {"max": max_id} if max_id else None
                r = requests.get(f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json", params=params,
                                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                                          "Accept": "application/json"}, timeout=15)
                r.raise_for_status()
                msgs = r.json().get("messages") or []
                if not msgs:
                    break
                out += msgs
                max_id = msgs[-1]["id"] - 1
                time.sleep(1)
            for m in out:
                sent = (((m.get("entities") or {}).get("sentiment") or {}) or {}).get("basic")
                user = m.get("user") or {}
                items.append({"src": "Stocktwits", "outlet": "$" + sym.split(".")[0], "id": f"st:{m['id']}",
                              "t": datetime.fromisoformat(m["created_at"].replace("Z", "+00:00")).timestamp(),
                              "text": strip(m.get("body") or "")[:700], "title": None, "url": f"https://stocktwits.com/message/{m['id']}",
                              "author": user.get("username"), "eng": ((m.get("likes") or {}).get("total") or 0), "lang": None, "bot": False,
                              "kind": "social", "tag": "bull" if sent == "Bullish" else "bear" if sent == "Bearish" else None})
            status["Stocktwits $" + sym.split(".")[0]] = len(out)
        except Exception as ex:  # noqa: BLE001
            status["Stocktwits $" + sym.split(".")[0]] = "lỗi: " + str(ex)[:80]
    return items, status


def fetch_bluesky() -> tuple[list, dict]:
    """Bluesky: tìm bài mới nhất theo từ khóa qua API công khai (không cần tài khoản)."""
    items, status = [], {}
    for q in ("bitcoin", "crypto", "ethereum"):
        last = None
        for host in ("https://public.api.bsky.app", "https://api.bsky.app"):
            try:
                r = requests.get(f"{host}/xrpc/app.bsky.feed.searchPosts", params={"q": q, "limit": 100, "sort": "latest"},
                                 headers={"User-Agent": UA, "Accept": "application/json"}, timeout=15)
                r.raise_for_status()
                posts = r.json().get("posts") or []
                for p_ in posts:
                    rec = p_.get("record") or {}
                    au = p_.get("author") or {}
                    langs = rec.get("langs") or []
                    items.append({"src": "Bluesky", "outlet": q, "id": "bs:" + p_.get("uri", ""),
                                  "t": datetime.fromisoformat(rec.get("createdAt", "1970-01-01T00:00:00Z").replace("Z", "+00:00")).timestamp(),
                                  "text": strip(rec.get("text") or "")[:700], "title": None,
                                  "url": "https://bsky.app/profile/" + au.get("handle", "") + "/post/" + p_.get("uri", "").rsplit("/", 1)[-1],
                                  "author": au.get("handle"), "eng": (p_.get("likeCount") or 0) + (p_.get("repostCount") or 0) + (p_.get("replyCount") or 0),
                                  "lang": langs[0][:2] if langs else None, "bot": False, "kind": "social"})
                status["Bluesky " + q] = len(posts)
                last = None
                break
            except Exception as ex:  # noqa: BLE001
                last = ex
        if last is not None:
            status["Bluesky " + q] = "lỗi: " + str(last)[:80]
        time.sleep(0.5)
    return items, status


def stocktwits_summary(store: dict) -> dict | None:
    """Tỷ lệ bài tự gắn Bullish trong số bài có gắn nhãn, 24 giờ qua và 24 giờ trước đó."""
    now = time.time()
    def part(lo, hi):
        tags = [x.get("tag") for x in store.values() if x.get("src") == "Stocktwits" and lo <= now - x["t"] < hi]
        b, e = tags.count("bull"), tags.count("bear")
        return {"n": len(tags), "bull": b, "bear": e, "bull_pct": round(100 * b / (b + e), 1) if b + e >= 10 else None}
    cur, prev = part(0, 86400), part(86400, 2 * 86400)
    if not cur["n"]:
        return None
    cur["prev_bull_pct"] = prev["bull_pct"]
    return cur


def fetch_telegram() -> tuple[list, dict]:
    items, status = [], {}
    for ch in TELEGRAM:
        try:
            r = requests.get(f"https://t.me/s/{ch}", headers={"User-Agent": UA}, timeout=15)
            r.raise_for_status()
            blocks = r.text.split('class="tgme_widget_message_wrap')[1:]
            n = 0
            for b in blocks:
                post = re.search(r'data-post="([^"]+)"', b)
                tm = re.search(r'<time[^>]*datetime="([^"]+)"', b)
                tx = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
                if not (post and tm and tx):
                    continue
                text = strip(tx.group(1))
                text = re.sub(r"@\w+\s*$", "", text).strip()
                try:
                    t = datetime.fromisoformat(tm.group(1)).timestamp()
                except ValueError:
                    continue
                items.append({"src": "Telegram", "outlet": "@" + ch, "id": "tg:" + post.group(1), "t": t, "text": text[:700],
                              "title": None, "url": "https://t.me/" + post.group(1), "author": ch, "eng": 0, "lang": None, "bot": False, "kind": "social"})
                n += 1
            status["TG @" + ch] = n
        except Exception as ex:  # noqa: BLE001
            status["TG @" + ch] = "lỗi: " + str(ex)[:80]
        time.sleep(0.5)
    return items, status


def fetch_lemmy_hn() -> tuple[list, dict]:
    items, status = [], {}
    for q in ["bitcoin", "crypto"]:
        try:
            j = get_json(f"https://lemmy.world/api/v3/search?q={q}&type_=All&sort=New&limit=50")
            for p in j.get("posts", []):
                items.append({"src": "Lemmy", "outlet": "lemmy.world", "id": "lp:" + str(p["post"]["id"]), "t": datetime.fromisoformat(p["post"]["published"].replace("Z", "+00:00")).timestamp(),
                              "text": strip(p["post"].get("name", "") + ". " + (p["post"].get("body") or "")), "title": p["post"].get("name"),
                              "url": p["post"].get("ap_id"), "author": (p.get("creator") or {}).get("name"), "eng": (p.get("counts") or {}).get("score", 0),
                              "lang": None, "bot": bool((p.get("creator") or {}).get("bot_account")), "kind": "social"})
            for c in j.get("comments", []):
                items.append({"src": "Lemmy", "outlet": "lemmy.world", "id": "lc:" + str(c["comment"]["id"]), "t": datetime.fromisoformat(c["comment"]["published"].replace("Z", "+00:00")).timestamp(),
                              "text": strip(c["comment"].get("content")), "title": None, "url": c["comment"].get("ap_id"),
                              "author": (c.get("creator") or {}).get("name"), "eng": (c.get("counts") or {}).get("score", 0),
                              "lang": None, "bot": bool((c.get("creator") or {}).get("bot_account")), "kind": "social"})
            status["Lemmy " + q] = len(j.get("posts", [])) + len(j.get("comments", []))
        except Exception as ex:  # noqa: BLE001
            status["Lemmy " + q] = "lỗi: " + str(ex)[:80]
    since = int(NOW) - 2 * 86400
    for q in ["bitcoin", "cryptocurrency", "ethereum"]:
        try:
            j = get_json(f"https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=(story,comment)&numericFilters=created_at_i>{since}&hitsPerPage=300")
            for h in j.get("hits", []):
                items.append({"src": "Hacker News", "outlet": "news.ycombinator.com", "id": "hn:" + h["objectID"], "t": h["created_at_i"],
                              "text": strip((h.get("title") or "") + ". " + (h.get("story_text") or "") if h.get("title") else h.get("comment_text")),
                              "title": h.get("title"), "url": "https://news.ycombinator.com/item?id=" + h["objectID"], "author": h.get("author"),
                              "eng": h.get("points") or 0, "lang": None, "bot": False, "kind": "social"})
            status["HN " + q] = len(j.get("hits", []))
        except Exception as ex:  # noqa: BLE001
            status["HN " + q] = "lỗi: " + str(ex)[:80]
    return items, status


# ---------------------------------------------------------------- tầng 2: lọc
RELEVANT = re.compile(r"\b(bitcoins?|btc|ethereum|ether|eth|solana|xrp|ripple|dogecoin|doge|stablecoins?|altcoins?|memecoins?|cryptocurrenc(y|ies)|crypto (market|markets|price|prices|exchange|exchanges|etf|etfs|asset|assets|investor|investors|trader|traders|trading|bull|bear|crash|winter|rally|regulation|industry|holders?|treasury|adoption)|defi|satoshis?|sats|hodl|coinbase|binance|bybit|kraken|saylor|microstrategy|ibit|halving|blockchain|on-?chain|spot etf|bitcoin etf)\b", re.I)
NOT_CRYPTO = re.compile(r"\b(cryptograph\w*|crypto librar\w*|crypto api|encryption|cipher)\b", re.I)
ADS = re.compile(r"(for sale|in stock|open-box|ready to ship|we accept|paid in btc|discount|giveaway|airdrop|promo code|referral|sign ?up bonus|join (our|my|us)|follow (us|me)|dm me|t\.me/|telegram group|whatsapp|free \d+|claim (your|now)|presale|subscribe)", re.I)
PRICE_BOT = re.compile(r"(price\s*[:：]|market cap\s*[:：]|【.*レポート】|\b24h\b.*%|▲|▼|🔼|🔽)", re.I)


FOREIGN = re.compile(r"\b(und|der|die|das|nicht|mit|auf|von|über|zu|ist|ein|eine|les|des|pour|avec|est|una|los|las|para|con|que|del|por|het|een|niet)\b", re.I)


def looks_foreign(s: str) -> bool:
    words = re.findall(r"[A-Za-zÀ-ÿ]+", s)
    return len(words) >= 6 and len(FOREIGN.findall(s)) / len(words) > 0.08


NEWS_CRYPTO = re.compile(r"crypto|bitcoin|\bbtc\b|ether|\beth\b|token|stablecoin|tether|usd[ct]\b|blockchain|defi\b|\bnfts?\b|\betfs?\b|coins?\b|"
                         r"zcash|cardano|\bada\b|solana|\bsol\b|xrp|ripple|binance|coinbase|kraken|wallet|mining|miners?\b|satoshi|altcoin|"
                         r"memecoin|web3|\bdaos?\b|on-?chain|ledger|exchange|\bsec\b|cftc|circle\b|saylor|doge|hyperliquid|polymarket|"
                         r"prediction market|digital asset|world liberty|wlfi|usd1|\blink\b|market maker|risk assets|tokeni[sz]|rwa\b|halving|layer[ -]?2|airdrop|staking|validator|mixer|tornado", re.I)
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")


def ascii_ratio(s: str) -> float:
    return sum(1 for ch in s if ord(ch) < 128) / max(1, len(s))


def norm(s: str) -> str:
    s = re.sub(r"https?://\S+|[#@]\w+", "", s.lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", s)).strip()[:90]


def clean(items: list) -> tuple[list, dict]:
    reasons = Counter()
    seen, per_author, kept = set(), Counter(), []
    for it in sorted(items, key=lambda x: -x["t"]):
        text = it.get("text") or ""
        core = re.sub(r"https?://\S+|[#@][\w.]+", "", text).strip()
        key = norm(text)
        if not key or key in seen or it["id"] in seen:
            reasons["trùng lặp"] += 1
            continue
        seen.update([key, it["id"]])
        digits = len(re.sub(r"[^0-9]", "", core)) / max(1, len(core))
        if it.get("bot") or PRICE_BOT.search(text) or (it["kind"] == "social" and digits > 0.18):
            reasons["bot / báo giá tự động"] += 1
            continue
        if ADS.search(text):
            reasons["quảng cáo / rao bán"] += 1
            continue
        plain = EMOJI_RE.sub("", core)
        if (it.get("lang") and it["lang"] != "en") or ascii_ratio(plain) < 0.9 or looks_foreign(plain):
            reasons["không phải tiếng Anh"] += 1
            continue
        if len(core) < 25:
            reasons["quá ngắn"] += 1
            continue
        if it["kind"] == "news" and not NEWS_CRYPTO.search(text):   # báo crypto đôi khi đăng tin game, AI, công nghệ chung
            reasons["không liên quan crypto"] += 1
            continue
        if it["kind"] == "social" and (not RELEVANT.search(text) or (NOT_CRYPTO.search(text) and not re.search(r"\b(bitcoin|btc|ethereum|eth|cryptocurrenc)", text, re.I))):
            reasons["không liên quan crypto"] += 1
            continue
        a = it["src"] + ":" + str(it.get("author"))
        per_author[a] += 1
        if it["kind"] == "social" and it["src"] not in ("YouTube", "Telegram") and per_author[a] > 3:
            reasons["cùng tác giả quá nhiều"] += 1
            continue
        kept.append(it)
    return kept, dict(reasons)


# ---------------------------------------------------------------- tầng 3b: mô hình AI (ONNX)
class Model:
    REPO = "Xenova/twitter-roberta-base-sentiment-latest"

    def __init__(self):
        import numpy as np  # noqa: F401
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        self.tok = Tokenizer.from_file(hf_hub_download(self.REPO, "tokenizer.json"))
        self.tok.enable_truncation(max_length=256)
        self.tok.enable_padding(pad_id=1, pad_token="<pad>")
        self.sess = ort.InferenceSession(hf_hub_download(self.REPO, "onnx/model_quantized.onnx"), providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        cfg = json.load(open(hf_hub_download(self.REPO, "config.json"), encoding="utf-8"))
        lab = {int(k): v.lower() for k, v in cfg["id2label"].items()}
        self.pos = next(k for k, v in lab.items() if "pos" in v)
        self.neg = next(k for k, v in lab.items() if "neg" in v)

    def score(self, texts: list[str]) -> list[float]:
        import numpy as np
        out = []
        for i in range(0, len(texts), 32):
            batch = [re.sub(r"https?://\S+", "http", re.sub(r"@\w+", "@user", t))[:600] for t in texts[i:i + 32]]
            enc = self.tok.encode_batch(batch)
            feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64), "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64)}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
            logits = self.sess.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            e = np.exp(logits - logits.max(axis=1, keepdims=True))
            p = e / e.sum(axis=1, keepdims=True)
            out += (p[:, self.pos] - p[:, self.neg]).tolist()
        return out


def hybrid(ai: float | None, lx: float) -> float:
    if ai is None:
        return lx
    if abs(ai) < 0.35 and abs(lx) >= 0.5:
        return lx * 0.8
    return 0.7 * ai + 0.3 * lx


# ---------------------------------------------------------------- tầng 4: tổng hợp
STOP = set("the a an and or but if then than that this these those there their they them is are was were be been being have has had do does did of to in on for with as at by from about into over after before under between out up down off so not no yes it its it's i you your we our us my me he she his her him what which who whom when where why how all any some more most other such only own same too very can will just should now also like get got one two new would could may might much many even back still well way make made think know see go going said says say really people time year years day days thing things lot good don't im i'm thats that's there's dont doesnt isnt via amp https http www com html week today amid while first post appeared submitted link comments reddit magazine next months here just watcher guru breaking video million billion thousand percent according reported report latest since around across continue read october november december january february march april june july august september monday tuesday wednesday thursday friday saturday sunday".split())
GENERIC = set("bitcoin btc crypto cryptocurrency cryptocurrencies ethereum eth coin coins blockchain price prices market markets news token tokens".split())
COINS = [("BTC", r"\b(bitcoin|btc|sats?|satoshi)\b"), ("ETH", r"\b(ethereum|eth|ether)\b"), ("SOL", r"\b(solana|sol)\b"), ("XRP", r"\b(xrp|ripple)\b"),
         ("DOGE", r"\b(dogecoin|doge)\b"), ("BNB", r"\b(bnb)\b"), ("ADA", r"\b(cardano|ada)\b"), ("Stablecoin", r"\b(stablecoins?|usdt|usdc|tether)\b")]
SRC_WEIGHT = {"Tin tức": 1.0, "Reddit": 1.0, "Stocktwits": 0.9, "YouTube": 0.8, "Telegram": 0.8, "Bluesky": 0.8, "Mastodon": 0.8, "Hacker News": 0.7, "Lemmy": 0.3}


def wmean(arr):
    w = sum(x["w"] for x in arr)
    return sum(x["s"] * x["w"] for x in arr) / w if w else None


def diverse(arr: list, post, n: int = 5, per_src: int = 2) -> list:
    """Chọn bài nổi bật nhưng tối đa 2 bài mỗi nguồn, để một nguồn đông bài (như Stocktwits) không lấn hết."""
    out, cnt = [], {}
    for x in arr:
        if cnt.get(x["src"], 0) < per_src:
            cnt[x["src"]] = cnt.get(x["src"], 0) + 1
            out.append(post(x))
        if len(out) >= n:
            break
    return out


def aggregate(kept: list) -> dict:
    for x in kept:
        x["w"] = (1 + math.log1p(max(0, x.get("eng") or 0))) * SRC_WEIGHT.get(x["src"], 1)
        x["label"] = "pos" if x["s"] > 0.15 else "neg" if x["s"] < -0.15 else "neu"
    last24 = [x for x in kept if NOW - x["t"] <= 86400]
    prev = [x for x in kept if 86400 < NOW - x["t"] <= 3 * 86400]
    base = last24 if len(last24) >= 40 else [x for x in kept if NOW - x["t"] <= 2 * 86400]

    def block(arr):
        if not arr:
            return None
        sc = wmean(arr)
        n = len(arr)
        return {"score": round(sc, 4), "index": round((sc + 1) * 50), "n": n,
                "pos": round(100 * sum(x["label"] == "pos" for x in arr) / n, 1),
                "neg": round(100 * sum(x["label"] == "neg" for x in arr) / n, 1),
                "neu": round(100 * sum(x["label"] == "neu" for x in arr) / n, 1)}

    days = defaultdict(list)
    for x in kept:
        days[datetime.fromtimestamp(x["t"], VN).strftime("%Y-%m-%d")].append(x)
    daily = [{"date": d, "n": len(a), "score": round(wmean(a), 4)} for d, a in sorted(days.items()) if len(a) >= 5][-7:]

    wc = Counter()
    for x in base:
        words = set(w.strip("-") for w in re.split(r"[^a-z0-9$-]+", re.sub(r"https?://\S+", " ", x["text"].lower())))
        wc.update(w for w in words if len(w) >= 4 and w not in STOP and w not in GENERIC and not w.isdigit())
    coins = sorted(((c, sum(1 for x in base if re.search(p, x["text"], re.I))) for c, p in COINS), key=lambda t: -t[1])

    def post(x):
        return {"src": x["src"], "outlet": x.get("outlet"), "t": int(x["t"] * 1000), "s": round(x["s"], 3),
                "text": (x.get("title") or x["text"])[:240], "url": x.get("url")}

    ranked = sorted([x for x in base if len(x["text"]) >= 40], key=lambda x: x["s"] * x["w"], reverse=True)
    by_src = defaultdict(list)
    for x in base:
        by_src[x["src"]].append(x)

    return {
        "all": block(base), "news": block([x for x in base if x["kind"] == "news"]), "social": block([x for x in base if x["kind"] == "social"]),
        "trend": round(wmean(last24) - wmean(prev), 4) if len(last24) >= 20 and len(prev) >= 20 else None,
        "window_hours": 24 if base is last24 else 48,
        "daily": daily,
        "sources": [{"src": k, "n": len(a), "score": round(wmean(a), 4)} for k, a in sorted(by_src.items(), key=lambda t: -len(t[1]))],
        "keywords": [[w, n] for w, n in wc.most_common(14) if n >= 3],
        "coins": [[c, n] for c, n in coins if n],
        "top_pos": diverse([x for x in ranked if x["label"] == "pos"], post),
        "top_neg": diverse([x for x in reversed(ranked) if x["label"] == "neg"], post),
    }


# ---------------------------------------------------------------- dịch sang tiếng Việt
# Sửa các thuật ngữ crypto mà dịch máy hay dịch sai
VI_FIX = [
    (r"cuộc biểu tình", "đà tăng"), (r"biểu tình", "đà tăng"), (r"cuộc tăng giá", "đà tăng"),
    (r"\bgiá thầu\b", "lệnh mua"), (r"thanh lý tài sản", "thanh lý vị thế"), (r"đồng xu", "đồng coin"),
    (r"\bcá voi\b", "cá voi (nhà đầu tư lớn)"), (r"bò tót", "phe tăng giá"), (r"\bnhững con bò\b", "phe tăng giá"),
    (r"\bnhững con gấu\b", "phe giảm giá"), (r"dòng chảy ra", "dòng tiền rút ra"), (r"dòng chảy vào", "dòng tiền vào"),
    (r"\bsàn giao dịch trao đổi\b", "sàn giao dịch"), (r"\bmã thông báo\b", "token"), (r"tiền điện tử ổn định", "stablecoin"),
]


def fix_vi(t: str) -> str:
    for pat, rep_ in VI_FIX:
        t = re.sub(pat, rep_, t, flags=re.I)
    return t


def translate_posts(posts: list) -> str:
    """Dịch tiêu đề các bài nổi bật bằng MyMemory (miễn phí), có bộ nhớ đệm và hạn mức ngày."""
    try:
        cache = json.load(open(TRANS_FILE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta = cache.get("_meta", {})
    used = meta.get("chars", 0) if meta.get("day") == day else 0
    texts = cache.get("texts", {})
    status = "ok"
    for p in posts:
        src = (p.get("text") or "")[:220]
        if not src:
            continue
        if src in texts:
            p["vi"] = texts[src]
            continue
        if used + len(src) > DAILY_CHAR_BUDGET:
            status = "hết hạn mức dịch hôm nay"
            continue
        try:
            j = get_json("https://api.mymemory.translated.net/get", params={"q": src, "langpair": "en|vi"})
            vi = (j.get("responseData") or {}).get("translatedText") or ""
            used += len(src)
            if j.get("quotaFinished") or j.get("responseStatus") not in (200, "200") or not vi or vi.upper().startswith("MYMEMORY WARNING"):
                status = "dịch vụ dịch từ chối: " + str(j.get("responseDetails") or j.get("responseStatus"))[:60]
                continue
            p["vi"] = texts[src] = fix_vi(html.unescape(vi))
            time.sleep(1)
        except Exception as ex:  # noqa: BLE001
            status = "lỗi dịch: " + str(ex)[:60]
    if len(texts) > 400:
        texts = dict(list(texts.items())[-400:])
    with open(TRANS_FILE, "w", encoding="utf-8") as f:
        json.dump({"_meta": {"day": day, "chars": used}, "texts": texts}, f, ensure_ascii=False)
    return status


# ---------------------------------------------------------------- vĩ mô (miễn phí, nhiều nguồn dự phòng)
FRED = {
    "WALCL": "Bảng cân đối Fed", "WTREGEN": "Tài khoản Kho bạc (TGA)", "RRPONTSYD": "Repo ngược (RRP)", "M2SL": "Cung tiền M2",
    "DGS2": "Lợi suất 2 năm", "DGS10": "Lợi suất 10 năm", "DFII10": "Lợi suất thực 10 năm", "DTWEXBGS": "Chỉ số USD",
    "VIXCLS": "VIX", "BAMLH0A0HYM2": "Chênh lệch trái phiếu rủi ro cao", "NASDAQ100": "Nasdaq 100", "DEXJPUS": "USD/JPY",
    "CBBTCUSD": "Bitcoin",
}
MACRO_CACHE = os.path.join(DATA, "macro_cache.json")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
_FREDGRAPH_OK = [True]   # tắt đường fredgraph ngay khi nó treo, để không tốn 30 giây cho mỗi chỉ số
_TREASURY = {}


def _day(s: str, fmt: str = "%Y-%m-%d") -> float:
    return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _finish(out: list) -> list:
    out = sorted({t: v for t, v in out}.items())
    if len(out) < 5:
        raise ValueError("không có dữ liệu")
    return out


def fred_api(sid: str, days: int) -> list:
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": sid, "api_key": FRED_KEY, "file_type": "json", "observation_start": _since(days).strftime("%Y-%m-%d")}, timeout=20)
    r.raise_for_status()
    return _finish([(_day(o["date"]), float(o["value"])) for o in r.json().get("observations", []) if o.get("value") not in (".", "", None)])


def fred_graph(sid: str, days: int) -> list:
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid, "cosd": _since(days).strftime("%Y-%m-%d")},
                         headers={"User-Agent": BROWSER_UA}, timeout=12)
        r.raise_for_status()
    except requests.RequestException:
        _FREDGRAPH_OK[0] = False
        raise
    out = []
    for row in list(csv.reader(r.text.splitlines()))[1:]:
        if len(row) >= 2 and row[1] not in (".", ""):
            try:
                out.append((_day(row[0]), float(row[1])))
            except ValueError:
                continue
    return _finish(out)


def yahoo(symbol: str, days: int) -> list:
    rng = "2y" if days > 365 else "1y"
    last = None
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}",
                             params={"range": rng, "interval": "1d"}, headers={"User-Agent": BROWSER_UA}, timeout=15)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            closes = res["indicators"]["quote"][0]["close"]
            out = [(float(t - t % 86400), float(c)) for t, c in zip(res["timestamp"], closes) if c is not None]
            return _finish(out)
        except Exception as ex:  # noqa: BLE001
            last = ex
    raise last


def treasury(kind: str, column: str, days: int) -> list:
    """Lợi suất trái phiếu Mỹ do Bộ Tài chính Mỹ công bố (cùng số liệu FRED dùng)."""
    if kind not in _TREASURY:
        rows = []
        y = datetime.now(timezone.utc).year
        for year in range(_since(days).year, y + 1):
            r = requests.get(f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all",
                             params={"type": kind, "field_tdr_date_value": year, "page": "", "_format": "csv"}, headers={"User-Agent": BROWSER_UA}, timeout=20)
            r.raise_for_status()
            rows += list(csv.DictReader(r.text.splitlines()))
        _TREASURY[kind] = rows
    out = []
    for row in _TREASURY[kind]:
        key = next((k for k in row if k and k.strip().lower() == column.lower()), None)
        if key and row.get(key) not in (None, "", "N/A"):
            try:
                out.append((_day(row["Date"], "%m/%d/%Y"), float(row[key])))
            except (KeyError, ValueError):
                continue
    return _finish(out)


def tga(days: int) -> list:
    r = requests.get("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance",
                     params={"filter": f"record_date:gte:{_since(days).strftime('%Y-%m-%d')},account_type:eq:Treasury General Account (TGA) Closing Balance",
                             "fields": "record_date,open_today_bal", "sort": "record_date", "page[size]": 1000}, timeout=20)
    r.raise_for_status()
    return _finish([(_day(x["record_date"]), float(x["open_today_bal"])) for x in r.json().get("data", []) if x.get("open_today_bal") not in (None, "", "null")])


def rrp(days: int) -> list:
    r = requests.get("https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json",
                     params={"startDate": _since(days).strftime("%Y-%m-%d")}, headers={"User-Agent": BROWSER_UA}, timeout=20)
    r.raise_for_status()
    tot = defaultdict(float)
    for op in r.json().get("repo", {}).get("operations", []):
        if op.get("operationDate") and op.get("totalAmtAccepted") is not None:
            tot[op["operationDate"]] += float(op["totalAmtAccepted"]) / 1e9
    return _finish([(_day(d), v) for d, v in tot.items()])


def coingecko_btc(days: int) -> list:
    r = requests.get("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart", params={"vs_currency": "usd", "days": min(days, 365), "interval": "daily"},
                     headers={"User-Agent": BROWSER_UA}, timeout=20)
    r.raise_for_status()
    return _finish([(float(int(t / 1000) - int(t / 1000) % 86400), float(v)) for t, v in r.json().get("prices", [])])


# Nguồn dự phòng khi FRED không trả lời. Tên hiển thị đổi theo nguồn nếu chỉ số khác (DXY thay cho rổ rộng, HYG thay cho chênh lệch).
FALLBACK = {
    "WTREGEN": [("Bộ Tài chính Mỹ", lambda d: tga(d))],
    "RRPONTSYD": [("Fed New York", lambda d: rrp(d))],
    "DGS2": [("Bộ Tài chính Mỹ", lambda d: treasury("daily_treasury_yield_curve", "2 Yr", d))],
    "DGS10": [("Bộ Tài chính Mỹ", lambda d: treasury("daily_treasury_yield_curve", "10 Yr", d))],
    "DFII10": [("Bộ Tài chính Mỹ", lambda d: treasury("daily_treasury_real_yield_curve", "10 YR", d))],
    "DTWEXBGS": [("Yahoo (DXY)", lambda d: yahoo("DX-Y.NYB", d))],
    "VIXCLS": [("Yahoo", lambda d: yahoo("^VIX", d))],
    "NASDAQ100": [("Yahoo", lambda d: yahoo("^NDX", d))],
    "DEXJPUS": [("Yahoo", lambda d: yahoo("JPY=X", d))],
    "CBBTCUSD": [("Yahoo", lambda d: yahoo("BTC-USD", d)), ("CoinGecko", lambda d: coingecko_btc(d))],
    "HYG": [("Yahoo", lambda d: yahoo("HYG", d))],
}


def macro_fetch() -> tuple[dict, dict, dict]:
    """Lấy từng chỉ số theo thứ tự: FRED có khóa -> FRED không khóa -> nguồn dự phòng -> bản lưu gần nhất (tối đa 10 ngày)."""
    try:
        with open(MACRO_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        cache = {}
    data, src, errs = {}, {}, {}
    t0 = time.time()
    for sid in list(FRED) + ["HYG"]:
        if sid == "HYG" and "BAMLH0A0HYM2" in data:
            continue   # đã có chênh lệch tín dụng thật, không cần quỹ HYG
        days = 900 if sid == "M2SL" else 420
        tries = []
        if sid in FRED and FRED_KEY:
            tries.append(("FRED", lambda d, s=sid: fred_api(s, d)))
        if sid in FRED and _FREDGRAPH_OK[0]:
            tries.append(("FRED", lambda d, s=sid: fred_graph(s, d)))
        tries += FALLBACK.get(sid, [])
        msg = []
        for name, fn in tries:
            if time.time() - t0 > 180:
                msg.append("hết giờ")
                break
            try:
                data[sid], src[sid] = fn(days), name
                break
            except Exception as ex:  # noqa: BLE001
                msg.append(f"{name}: {str(ex)[:70]}")
            time.sleep(0.3)
        if sid not in data:
            c = cache.get(sid)
            if c and c.get("saved", 0) > time.time() - 10 * 86400:
                data[sid], src[sid] = [tuple(x) for x in c["series"]], c["src"] + " (bản lưu)"
            if sid not in data:
                errs[sid] = "; ".join(msg)[:160] if msg else "chỉ có trên FRED, mà FRED không trả lời (thêm khóa FRED_API_KEY để lấy)"
    for sid, ser in data.items():
        if not src[sid].endswith("(bản lưu)"):
            cache[sid] = {"saved": time.time(), "src": src[sid], "series": [[int(t), v] for t, v in ser]}
    try:
        with open(MACRO_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, separators=(",", ":"))
    except OSError:
        pass
    print(f"Vĩ mô: {len(data)} chỉ số, lỗi {len(errs)}, {time.time() - t0:.0f} giây")
    return data, src, errs


def at_or_before(series: list, t: float):
    best = None
    for ts, v in series:
        if ts <= t:
            best = v
        else:
            break
    return best


def change(series: list, days: int, pct: bool = True):
    if not series:
        return None
    t, v = series[-1]
    old = at_or_before(series, t - days * 86400)
    if old in (None, 0):
        return None
    return (v / old - 1) * 100 if pct else v - old


def thin(series: list, n: int = 90) -> list:
    step = max(1, math.ceil(len(series) / n))
    pts = series[::step]
    if pts[-1] != series[-1]:
        pts.append(series[-1])
    return [[int(t * 1000), round(v, 4)] for t, v in pts]


def corr(a: list, b: list, days: int = 30):
    da, db = dict(a), dict(b)
    common = sorted(set(da) & set(db))
    common = [t for t in common if t >= common[-1] - days * 86400] if common else []
    if len(common) < 12:
        return None
    ra = [da[common[i]] / da[common[i - 1]] - 1 for i in range(1, len(common))]
    rb = [db[common[i]] / db[common[i - 1]] - 1 for i in range(1, len(common))]
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = math.sqrt(sum((x - ma) ** 2 for x in ra)), math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (va * vb) if va and vb else None


def label_of(score: int) -> str:
    return "Thuận lợi" if score > 0 else "Bất lợi" if score < 0 else "Trung tính"


def macro() -> dict:
    data, src, errs = macro_fetch()
    groups = []
    f1 = lambda v, d=1: None if v is None else round(v, d)  # noqa: E731

    # 1) Thanh khoản
    items, score, notes, chart = [], 0, [], None
    if all(k in data for k in ("WALCL", "WTREGEN", "RRPONTSYD")):
        net = []
        for t, w in data["WALCL"]:
            tga, rrp = at_or_before(data["WTREGEN"], t), at_or_before(data["RRPONTSYD"], t)
            if tga is not None and rrp is not None:
                net.append((t, w / 1000 - tga / 1000 - rrp))
        c4, c13 = change(net, 28), change(net, 91)
        items.append({"name": "Thanh khoản ròng của Fed", "value": round(net[-1][1]), "unit": " tỷ $", "ch": f1(c4, 2), "chLabel": "4 tuần", "ch2": f1(c13, 2), "ch2Label": "13 tuần", "src": src["WALCL"]})
        if c4 is not None:
            score += 1 if c4 > 1 else -1 if c4 < -1 else 0
            notes.append(f"thanh khoản ròng {'tăng' if c4 > 0 else 'giảm'} {abs(c4):.1f}% trong 4 tuần")
        chart = {"name": "Thanh khoản ròng (tỷ $)", "series": thin(net)}
    elif "WTREGEN" in data and "RRPONTSYD" in data:
        # Thiếu bảng cân đối Fed: theo dõi phần tiền bị hút khỏi hệ thống ngân hàng (TGA + RRP). Tăng là bất lợi.
        drain = []
        for t, g in data["WTREGEN"]:
            r_ = at_or_before(data["RRPONTSYD"], t)
            if r_ is not None:
                drain.append((t, g / 1000 + r_))
        d4 = change(drain, 28, pct=False)
        items.append({"name": "Tiền bị hút khỏi hệ thống (TGA + RRP)", "value": round(drain[-1][1]), "unit": " tỷ $",
                      "ch": None if d4 is None else round(d4), "chLabel": "tỷ $, 4 tuần", "src": src["WTREGEN"]})
        if d4 is not None:
            score += -1 if d4 > 100 else 1 if d4 < -100 else 0
            notes.append(f"TGA + RRP {'tăng' if d4 > 0 else 'giảm'} {abs(d4):.0f} tỷ $ trong 4 tuần ({'hút' if d4 > 0 else 'bơm'} tiền khỏi hệ thống)")
        chart = {"name": "TGA + RRP (tỷ $), tăng là hút tiền", "series": thin(drain)}
    if "M2SL" in data:
        y = change(data["M2SL"], 365)
        items.append({"name": "Cung tiền M2", "value": round(data["M2SL"][-1][1]), "unit": " tỷ $", "ch": f1(y), "chLabel": "1 năm", "asof": int(data["M2SL"][-1][0] * 1000), "src": src["M2SL"]})
        if y is not None:
            score += 1 if y > 5 else -1 if y < 0 else 0
            notes.append(f"M2 {'tăng' if y >= 0 else 'giảm'} {abs(y):.1f}% so với cùng kỳ")
    groups.append({"key": "liquidity", "name": "Thanh khoản", "score": max(-2, min(2, score)), "items": items, "chart": chart, "notes": notes})

    # 2) Giá của tiền
    items, score, notes, chart = [], 0, [], None
    for sid, nm in (("DGS2", "Lợi suất 2 năm"), ("DGS10", "Lợi suất 10 năm"), ("DFII10", "Lợi suất thực 10 năm")):
        if sid in data:
            bp = change(data[sid], 30, pct=False)
            items.append({"name": nm, "value": data[sid][-1][1], "unit": "%", "ch": None if bp is None else round(bp * 100), "chLabel": "điểm cơ bản, 1 tháng", "src": src[sid]})
    if "DFII10" in data:
        bp = change(data["DFII10"], 30, pct=False)
        if bp is not None:
            score += -1 if bp > 0.2 else 1 if bp < -0.2 else 0
            notes.append(f"lợi suất thực {'tăng' if bp > 0 else 'giảm'} {abs(bp) * 100:.0f} điểm cơ bản trong tháng")
        chart = {"name": "Lợi suất thực 10 năm (%)", "series": thin(data["DFII10"])}
    if "DTWEXBGS" in data:
        c = change(data["DTWEXBGS"], 30)
        items.append({"name": "Chỉ số USD (DXY)" if "DXY" in src["DTWEXBGS"] else "Chỉ số USD (rổ rộng)", "value": data["DTWEXBGS"][-1][1], "unit": "", "ch": f1(c, 2), "chLabel": "1 tháng", "src": src["DTWEXBGS"]})
        if c is not None:
            score += -1 if c > 1.5 else 1 if c < -1.5 else 0
            notes.append(f"USD {'mạnh lên' if c > 0 else 'yếu đi'} {abs(c):.1f}% trong tháng")
    groups.append({"key": "rates", "name": "Lãi suất và USD", "score": max(-2, min(2, score)), "items": items, "chart": chart, "notes": notes})

    # 3) Khẩu vị rủi ro
    items, score, notes, chart = [], 0, [], None
    if "VIXCLS" in data:
        v = data["VIXCLS"][-1][1]
        items.append({"name": "VIX", "value": v, "unit": "", "ch": f1(change(data["VIXCLS"], 30, pct=False)), "chLabel": "điểm, 1 tháng", "src": src["VIXCLS"]})
        score += -2 if v >= 30 else -1 if v >= 22 else 1 if v < 15 else 0
        notes.append(f"VIX ở {v:.1f}" + (" (căng thẳng)" if v >= 22 else " (bình yên)" if v < 15 else ""))
        chart = {"name": "VIX", "series": thin(data["VIXCLS"])}
    if "BAMLH0A0HYM2" in data:
        hy = data["BAMLH0A0HYM2"][-1][1]
        d = change(data["BAMLH0A0HYM2"], 30, pct=False)
        items.append({"name": "Chênh lệch trái phiếu rủi ro cao", "value": hy, "unit": "%", "ch": None if d is None else round(d * 100), "chLabel": "điểm cơ bản, 1 tháng", "src": src["BAMLH0A0HYM2"]})
        if d is not None and d > 0.5:
            score -= 1
            notes.append("chênh lệch tín dụng giãn rộng nhanh")
    elif "HYG" in data:
        c = change(data["HYG"], 30)
        items.append({"name": "Quỹ trái phiếu rủi ro cao (HYG)", "value": data["HYG"][-1][1], "unit": " $", "ch": f1(c, 2), "chLabel": "1 tháng", "src": src["HYG"]})
        if c is not None and c < -2:
            score -= 1
            notes.append(f"trái phiếu rủi ro cao bị bán, HYG giảm {abs(c):.1f}% trong tháng")
    if "DEXJPUS" in data:
        c = change(data["DEXJPUS"], 30)
        items.append({"name": "USD/JPY", "value": data["DEXJPUS"][-1][1], "unit": "", "ch": f1(c, 2), "chLabel": "1 tháng", "src": src["DEXJPUS"]})
        if c is not None and c < -4:
            score -= 1
            notes.append(f"yên Nhật tăng mạnh {abs(c):.1f}% trong tháng: nguy cơ tháo vốn vay rẻ bằng yên")
    groups.append({"key": "risk", "name": "Khẩu vị rủi ro", "score": max(-2, min(2, score)), "items": items, "chart": chart, "notes": notes})

    # 4) Chứng khoán và tương quan
    items, score, notes, chart = [], 0, [], None
    if "NASDAQ100" in data and "CBBTCUSD" in data:
        n7, b7 = change(data["NASDAQ100"], 7), change(data["CBBTCUSD"], 7)
        n30, b30 = change(data["NASDAQ100"], 30), change(data["CBBTCUSD"], 30)
        rho = corr(data["NASDAQ100"], data["CBBTCUSD"], 45)
        items += [{"name": "Nasdaq 100", "value": data["NASDAQ100"][-1][1], "unit": "", "ch": f1(n30, 1), "chLabel": "1 tháng", "ch2": f1(n7, 1), "ch2Label": "1 tuần", "src": src["NASDAQ100"]},
                  {"name": "Bitcoin", "value": data["CBBTCUSD"][-1][1], "unit": " $", "ch": f1(b30, 1), "chLabel": "1 tháng", "ch2": f1(b7, 1), "ch2Label": "1 tuần", "src": src["CBBTCUSD"]},
                  {"name": "Tương quan BTC–Nasdaq (45 ngày)", "value": None if rho is None else round(rho, 2), "unit": "", "ch": None, "chLabel": ""}]
        if n30 is not None:
            score += 1 if n30 > 3 else -1 if n30 < -5 else 0
        if n30 is not None and b30 is not None and n30 - b30 > 6:
            score -= 1
            notes.append(f"Nasdaq {n30:+.1f}% nhưng BTC {b30:+.1f}% trong tháng: tiền vào crypto yếu")
        elif n30 is not None and b30 is not None and b30 - n30 > 6:
            notes.append(f"BTC mạnh hơn Nasdaq ({b30:+.1f}% so với {n30:+.1f}% trong tháng)")
        if rho is not None:
            notes.append(f"tương quan với Nasdaq {'cao' if rho > 0.5 else 'thấp' if rho < 0.2 else 'vừa'} ({rho:.2f})")
        nb = [(t, v) for t, v in data["NASDAQ100"] if t >= data["NASDAQ100"][-1][0] - 180 * 86400]
        bb = [(t, v) for t, v in data["CBBTCUSD"] if t >= data["CBBTCUSD"][-1][0] - 180 * 86400]
        chart = {"name": "BTC và Nasdaq, 180 ngày (gốc = 100)",
                 "series": [[ms, round(v / nb[0][1] * 100, 2)] for ms, v in thin(nb)],
                 "series2": [[ms, round(v / bb[0][1] * 100, 2)] for ms, v in thin(bb)]}
    groups.append({"key": "equity", "name": "Chứng khoán và tương quan", "score": max(-2, min(2, score)), "items": items, "chart": chart, "notes": notes})

    for g in groups:
        g["label"] = label_of(g["score"])
        g["notes"] = [re.sub(r"(\d)\.(\d)", r"\1,\2", n) for n in g["notes"]]
    total = sum(g["score"] for g in groups if g["items"])
    asof = max((v[-1][0] for k, v in data.items() if k not in ("M2SL", "WALCL", "WTREGEN")), default=None)
    names = dict(FRED, HYG="Quỹ HYG")
    return {"sources": sorted({v.replace(" (bản lưu)", "") for v in src.values()}), "missing": [names[k] for k in errs if k not in data and not (k == "BAMLH0A0HYM2" and "HYG" in data) and not (k == "HYG" and "BAMLH0A0HYM2" in data)],
            "have": len(data), "score": total, "label": "Thuận lợi" if total >= 2 else "Bất lợi" if total <= -2 else "Trung tính",
            "groups": groups, "errors": {names[k]: v for k, v in errs.items()}, "asof": int(asof * 1000) if asof else None}


# ---------------------------------------------------------------- chạy
def stored_ok(x: dict) -> bool:
    """Áp lại bộ lọc hiện tại cho bài đã lưu từ trước (bộ lọc có thể đã chặt hơn)."""
    text = x.get("text") or ""
    if x.get("kind") == "news":
        return bool(NEWS_CRYPTO.search(text))
    plain = EMOJI_RE.sub("", re.sub(r"https?://\S+|[#@][\w.]+", "", text))
    if ascii_ratio(plain) < 0.9 or looks_foreign(plain):
        return False
    return bool(RELEVANT.search(text)) and not (NOT_CRYPTO.search(text) and not re.search(r"\b(bitcoin|btc|ethereum|eth|cryptocurrenc)", text, re.I))


def load_items() -> dict:
    out = {}
    if os.path.exists(ITEMS_FILE):
        with open(ITEMS_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                    x["text"] = re.sub(r"\s{2,}", " ", BOILER.sub("", x.get("text") or "")).strip()   # dọn bài cũ lưu trước khi có bộ lọc mới
                    if not stored_ok(x):
                        continue
                    if x.get("s") is not None:   # chấm lại phần từ điển để mọi bài dùng bộ quy tắc mới nhất
                        x["lex"] = round(lex_score(x["text"]), 4)
                        x["s"] = round(hybrid(x.get("ai"), x["lex"]), 4)
                    out[x["id"]] = x
                except json.JSONDecodeError:
                    continue
    return out


def main() -> int:
    os.makedirs(DATA, exist_ok=True)
    started = time.time()
    raw, status = [], {}
    for fn in (fetch_rss, fetch_reddit, fetch_mastodon, fetch_lemmy_hn, fetch_youtube, fetch_telegram, fetch_stocktwits, fetch_bluesky):
        a, st = fn()
        raw += a
        status.update(st)
    print(f"Thu thập: {len(raw)} bài thô")

    kept, reasons = clean(raw)
    store = load_items()
    new = [x for x in kept if x["id"] not in store]
    print(f"Lọc: giữ {len(kept)}, mới {len(new)}")

    model_status = "từ điển"
    ai_scores = [None] * len(new)
    if new:
        try:
            m = Model()
            ai_scores = m.score([x["text"] for x in new])
            model_status = "AI + từ điển"
        except Exception as ex:  # noqa: BLE001
            model_status = "từ điển (AI lỗi: " + str(ex)[:120] + ")"
            print("Không chạy được mô hình:", ex, file=sys.stderr)
    for x, a in zip(new, ai_scores):
        lx = lex_score(x["text"])
        x["lex"] = round(lx, 4)
        x["ai"] = None if a is None else round(a, 4)
        x["s"] = round(hybrid(a, lx), 4)
        store[x["id"]] = {k: x.get(k) for k in ("id", "src", "outlet", "t", "text", "title", "url", "author", "eng", "kind", "lex", "ai", "s", "tag")}

    cutoff = NOW - KEEP_DAYS * 86400
    store = {k: v for k, v in store.items() if v["t"] >= cutoff}
    with open(ITEMS_FILE, "w", encoding="utf-8") as f:
        for v in sorted(store.values(), key=lambda v: v["t"]):
            f.write(json.dumps(v, ensure_ascii=False) + "\n")

    with_ai = sum(1 for v in store.values() if v.get("ai") is not None)
    if store and with_ai:
        model_status = f"AI + từ điển ({round(100 * with_ai / len(store))}% số bài có điểm AI)" + ("" if not model_status.startswith("từ điển (AI lỗi") else "; lần chạy này AI lỗi")
    agg = aggregate(list(store.values()))
    agg["translation"] = translate_posts(agg["top_pos"] + agg["top_neg"])
    agg["stocktwits"] = stocktwits_summary(store)
    try:
        agg["macro"] = macro()
    except Exception as ex:  # noqa: BLE001
        agg["macro"] = {"error": str(ex)[:200]}
    stamp = datetime.now(timezone.utc)
    latest = {"generated_at": stamp.isoformat(timespec="seconds"), "scoring": model_status, "raw_count": len(raw), "kept_count": len(kept),
              "new_count": len(new), "stored_7d": len(store), "filter_reasons": reasons, "source_status": status,
              "runtime_sec": round(time.time() - started, 1), **agg}
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=1)

    new_file = not os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time_utc", "index_all", "score_all", "n_all", "index_news", "index_social", "pos_pct", "neg_pct"])
        a, nw, so = agg["all"] or {}, agg["news"] or {}, agg["social"] or {}
        w.writerow([stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), a.get("index", ""), a.get("score", ""), a.get("n", ""), nw.get("index", ""), so.get("index", ""), a.get("pos", ""), a.get("neg", "")])

    # Tầng 4 trên máy chủ và cảnh báo: lỗi ở đây không được làm hỏng phần đã lưu ở trên.
    try:
        import market
        with open(HISTORY_FILE, encoding="utf-8") as f:
            text_hist = [float(r["index_all"]) for r in csv.DictReader(f) if r.get("index_all") not in (None, "")]
        latest["market"] = market.run(latest, text_hist)
    except Exception as ex:  # noqa: BLE001
        latest["market"] = {"error": str(ex)[:200]}
        print("Lỗi chỉ số tổng hợp:", ex, file=sys.stderr)
    try:
        import events
        latest["events"] = events.run(latest, store)
    except Exception as ex:  # noqa: BLE001
        latest["events"] = {"error": str(ex)[:200]}
        print("Lỗi phần sự kiện:", ex, file=sys.stderr)
    try:
        import history_test
        latest["history_test"] = history_test.run()
    except Exception as ex:  # noqa: BLE001
        latest["history_test"] = {"error": str(ex)[:200]}
        print("Lỗi kiểm chứng lịch sử:", ex, file=sys.stderr)
    try:
        import alerts
        latest["alerts"] = alerts.run(latest)
    except Exception as ex:  # noqa: BLE001
        latest["alerts"] = {"error": str(ex)[:200]}
        print("Lỗi cảnh báo:", ex, file=sys.stderr)
    latest["runtime_sec"] = round(time.time() - started, 1)
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: latest[k] for k in ("scoring", "kept_count", "new_count", "stored_7d")}, ensure_ascii=False), agg["all"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
