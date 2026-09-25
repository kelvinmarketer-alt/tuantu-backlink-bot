#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tuantu-backlink-bot
===================
Web có bài mới (published) → tự tạo 1 bài Telegra.ph tóm tắt + backlink về bài gốc
→ ghi log vào Google Sheet (1 tab duy nhất). Chạy bằng GitHub Actions cron.

Nguồn bài: WP REST /wp-json/wp/v2/posts (chỉ trả bài ĐÃ ĐĂNG — nháp không tính).
Chống trùng: đọc cột "URL bài gốc" trong tab log, bài nào có rồi thì bỏ qua.
Auth Sheet: tái dùng service account của tuantu-index-bot (GCP_SA_JSON) — nhớ share Sheet cho nó.

ENV (đặt ở GitHub Secrets):
  SITE_URL           = https://nongsantuantuhanoi.vn/
  SHEET_ID           = 1huOAJ3z9og4Rah32rIvh4olqb1ue0R9vx6YuvCijGg4
  SHEET_TAB          = Backlink Log            (tuỳ chọn)
  GCP_SA_JSON        = <nội dung JSON service account>   (giống index-bot)
  TELEGRAPH_TOKEN    = <token telegra.ph>       (tuỳ chọn — trống thì tự tạo account, in token ra log để lưu lại)
  MAX_NEW            = 8                         (tuỳ chọn — trần bài xử lý mỗi lần)
  TELEGRAM_TOKEN     = ...                       (tuỳ chọn — báo Telegram)
  TELEGRAM_CHAT_ID   = ...                       (tuỳ chọn)
"""
import os, re, json, html, time, datetime
import xml.etree.ElementTree as ET
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

SITE_URL        = os.environ["SITE_URL"].strip().rstrip("/") + "/"
SHEET_ID        = os.environ["SHEET_ID"].strip()
SHEET_TAB       = os.environ.get("SHEET_TAB", "Backlink Log").strip()
SA_JSON         = os.environ["GCP_SA_JSON"]
TELEGRAPH_TOKEN = os.environ.get("TELEGRAPH_TOKEN", "").strip()
MAX_NEW         = int(os.environ.get("MAX_NEW", "8"))
TG_TOKEN        = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT         = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TZ_OFFSET       = int(os.environ.get("TZ_OFFSET_HOURS", "7"))

BRAND = "Nông Sản Tuấn Tú Hà Nội"
LANDING = SITE_URL + "nong-san-tuan-tu-ha-noi/"
BANG_GIA = SITE_URL + "bang-gia/"
TG_API = "https://api.telegra.ph"
HEADERS = ["Ngày", "Tên bài", "URL bài gốc", "URL Telegra.ph", "Trạng thái"]


# ---------------- Google Sheets ----------------
def sheets_client():
    info = json.loads(SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def ensure_tab(svc):
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    tabs = [s["properties"]["title"] for s in meta["sheets"]]
    if SHEET_TAB not in tabs:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_TAB}}}]}).execute()
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1",
            valueInputOption="RAW", body={"values": [HEADERS]}).execute()


def existing_urls(svc):
    """Cột C = URL bài gốc → set để chống trùng."""
    try:
        r = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!C2:C").execute()
        return {row[0].strip() for row in r.get("values", []) if row}
    except Exception:
        return set()


def append_rows(svc, rows):
    svc.spreadsheets().values().append(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": rows}).execute()


# ---------------- WordPress REST ----------------
WP_HEADERS = {
    # Giả trình duyệt VN để né WAF/CDN (QUIC.cloud) chặn IP datacenter (giống tuantu-index-bot).
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi,en;q=0.9",
}

# Dấu hiệu trang "challenge" của QUIC.cloud/Cloudflare (không phải nội dung thật).
_CHALLENGE = (b"One moment", b"Just a moment", b"challenge-platform", b"cf-browser-verification",
              b"Checking your browser", b"Attention Required")


def _fetch(url, params=None, tries=5):
    """GET có retry backoff tăng dần + phát hiện trang challenge WAF. Trả Response 200 (nội dung thật) hoặc None."""
    last = ""
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=WP_HEADERS, timeout=30)
            head = r.content[:800]
            is_challenge = any(m in head for m in _CHALLENGE)
            if r.status_code == 200 and not is_challenge:
                return r
            last = f"HTTP {r.status_code}" + (" (WAF challenge)" if is_challenge else "")
        except Exception as e:
            last = type(e).__name__
        time.sleep(3 * (i + 1))   # 3s, 6s, 9s, 12s, 15s — chờ qua cửa sổ bị chặn
    print(f"  [fetch fail] {url} -> {last}")
    return None


def _from_rest(limit):
    r = _fetch(SITE_URL + "wp-json/wp/v2/posts",
               {"per_page": limit, "orderby": "date", "order": "desc",
                "_fields": "title,link,excerpt,date"})
    if not r:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, list):   # WAF trả object lỗi -> coi như thất bại
        return None
    out = []
    for p in data:
        if not isinstance(p, dict):
            continue
        title = html.unescape(re.sub("<[^>]+>", "", (p.get("title") or {}).get("rendered", ""))).strip()
        exc = html.unescape(re.sub("<[^>]+>", " ", (p.get("excerpt") or {}).get("rendered", ""))).strip()
        out.append({"title": title, "url": (p.get("link") or "").strip(),
                    "excerpt": re.sub(r"\s+", " ", exc)})
    return out or None


def _from_rss(limit):
    """Fallback: đọc RSS feed (title/link/description) — thường được CDN cache nên IP nào cũng đọc được."""
    r = _fetch(SITE_URL + "feed/")
    if not r:
        return None
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return None
    out = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        title = html.unescape((item.findtext("title") or "").strip())
        desc = item.findtext("description") or ""
        exc = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", desc))).strip()
        if link:
            out.append({"title": title, "url": link, "excerpt": exc})
        if len(out) >= limit:
            break
    return out or None


def _from_sitemap(limit):
    """Fallback cuối: post-sitemap.xml (file tĩnh, CDN cache → IP nào cũng đọc được, như bot index).
    Lấy URL mới nhất; title/excerpt cào từ trang HTML (trang đã cache nên không bị WAF chặn)."""
    r = _fetch(SITE_URL + "post-sitemap.xml")
    if not r:
        return None
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return None
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    items = []
    for u in root.findall(".//s:url", ns):
        loc = (u.findtext("s:loc", default="", namespaces=ns) or "").strip()
        lm = (u.findtext("s:lastmod", default="", namespaces=ns) or "").strip()
        if loc:
            items.append((lm, loc))
    if not items:
        return None
    items.sort(key=lambda x: x[0], reverse=True)   # mới nhất trước
    out = []
    for lm, loc in items[:limit]:
        slug = loc.rstrip("/").split("/")[-1]
        out.append({"title": slug.replace("-", " ").strip().title(), "url": loc, "excerpt": ""})
    return out or None


def enrich(post):
    """Bù title/excerpt từ trang HTML (đã cache) nếu nguồn không có — chỉ gọi cho bài MỚI."""
    if post.get("excerpt"):
        return post
    r = _fetch(post["url"])
    if not r:
        return post
    h = r.text
    m = re.search(r"<title[^>]*>(.*?)</title>", h, re.S | re.I)
    if m:
        t = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        t = re.split(r"\s[–|]\s|\s-\s", t)[0].strip() or t     # bỏ đuôi "| Tên site"
        if t:
            post["title"] = t
    d = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', h, re.S | re.I)
    if d:
        post["excerpt"] = html.unescape(d.group(1)).strip()
    return post


def fetch_new_posts(limit=20):
    """Lấy bài mới nhất. REST → RSS → sitemap. None nếu tất cả fail."""
    posts = _from_rest(limit)
    if posts is None:
        print("  REST bị chặn → thử RSS feed")
        posts = _from_rss(limit)
    if posts is None:
        print("  RSS bị chặn → thử post-sitemap.xml")
        posts = _from_sitemap(limit)
    return posts


# ---------------- Telegra.ph ----------------
def tg(method, **fields):
    r = requests.post(f"{TG_API}/{method}", data=fields, timeout=30)
    return r.json()


def get_token():
    if TELEGRAPH_TOKEN:
        return TELEGRAPH_TOKEN
    res = tg("createAccount", short_name="NongSanTuanTu",
             author_name=BRAND, author_url=SITE_URL)
    tok = res["result"]["access_token"]
    print(f"[!] Chưa có TELEGRAPH_TOKEN — vừa tạo mới. LƯU token này vào Secrets để dùng lại:\n    {tok}")
    return tok


def p(*c): return {"tag": "p", "children": list(c)}
def a(t, h): return {"tag": "a", "attrs": {"href": h}, "children": [t]}
def strong(t): return {"tag": "strong", "children": [t]}


def build_content(post, idx):
    # Anchor về bài gốc xoay vòng cho tự nhiên.
    read_anchors = ["Đọc bài đầy đủ tại đây", "Xem chi tiết bài viết", "Đọc tiếp trên website"]
    ra = read_anchors[idx % len(read_anchors)]
    summary = post["excerpt"] or (post["title"] + ".")
    if len(summary) > 400:
        summary = summary[:397] + "..."
    return [
        p(summary),
        p(ra + ": ", a(post["title"], post["url"]), "."),
        p("Bài viết thuộc ", a(BRAND, LANDING),
          " — đơn vị cung cấp nông sản sỉ cho nhà hàng, khách sạn tại Hà Nội. ",
          "Tham khảo ", a("bảng giá nông sản", BANG_GIA),
          " hoặc liên hệ hotline/Zalo 0398.055.632 để nhận báo giá."),
    ]


def create_telegraph(token, post, idx):
    res = tg("createPage", access_token=token, title=post["title"][:256],
             author_name=BRAND, author_url=SITE_URL,
             content=json.dumps(build_content(post, idx), ensure_ascii=False),
             return_content="false")
    if res.get("ok"):
        return res["result"]["url"]
    raise RuntimeError(f"Telegraph fail: {res}")


# ---------------- Telegram (tuỳ chọn) ----------------
def notify(msg):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": msg,
                            "parse_mode": "HTML", "disable_web_page_preview": "true"}, timeout=20)
        print(f"[TG] status={r.status_code} body={r.text[:300]}")
    except Exception as e:
        print("Telegram err:", e)


# ---------------- Main ----------------
def main():
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=TZ_OFFSET)
    day = now.strftime("%Y-%m-%d %H:%M")
    svc = sheets_client()
    ensure_tab(svc)
    seen = existing_urls(svc)

    posts = fetch_new_posts(limit=20)
    if posts is None:
        print("Không lấy được danh sách bài (REST + RSS đều bị chặn).")
        notify(f"⚠️ <b>Backlink bot</b> ({day})\nKhông lấy được danh sách bài (site chặn request). Sẽ thử lại lần chạy sau.")
        return
    new = [p for p in posts if p["url"] and p["url"] not in seen][:MAX_NEW]
    if not new:
        print("Không có bài mới. Đã log:", len(seen))
        notify(f"🔗 <b>Backlink bot</b> ({day})\n✅ Đã chạy — không có bài mới. Tổng backlink đã tạo: <b>{len(seen)}</b>.")
        return

    token = get_token()
    rows, ok = [], 0
    for i, post in enumerate(new):
        try:
            post = enrich(post)   # bù title/excerpt từ trang cache nếu nguồn thiếu
            tg_url = create_telegraph(token, post, i)
            rows.append([day, post["title"], post["url"], tg_url, "OK"])
            ok += 1
            print("✅", tg_url, "→", post["url"])
            time.sleep(1)
        except Exception as e:
            rows.append([day, post["title"], post["url"], "", f"LỖI: {e}"])
            print("❌", post["url"], e)

    append_rows(svc, rows)
    notify(f"🔗 <b>Backlink bot</b> ({day})\nĐã tạo <b>{ok}</b> backlink Telegra.ph cho bài mới.\nLog: Sheet tab “{SHEET_TAB}”.")
    print(f"Xong. Tạo {ok}/{len(new)} backlink, ghi {len(rows)} dòng log.")


if __name__ == "__main__":
    main()
