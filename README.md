# tuantu-backlink-bot

Web **nongsantuantuhanoi.vn** có bài mới (đã đăng) → bot tự tạo **1 bài Telegra.ph** tóm tắt + backlink về bài gốc → ghi **log vào Google Sheet** (1 tab duy nhất). Chạy tự động bằng GitHub Actions cron mỗi sáng.

- Nguồn bài: WP REST `/wp-json/wp/v2/posts` (chỉ bài ĐÃ ĐĂNG; nháp không tính).
- Chống trùng: đọc cột "URL bài gốc" trong tab log; bài đã có thì bỏ qua.
- Auth Sheet: **tái dùng service account của `tuantu-index-bot`** (secret `GCP_SA_JSON`).

## Sheet đích
`1huOAJ3z9og4Rah32rIvh4olqb1ue0R9vx6YuvCijGg4` — tab **`Backlink Log`** (bot tự tạo nếu chưa có).
Cột: Ngày · Tên bài · URL bài gốc · URL Telegra.ph · Trạng thái.

## Cài đặt (1 lần)

1. **Share Sheet cho service account.** Mở Sheet → Share → dán **email service account** (giống bot index, dạng `xxx@yyy.iam.gserviceaccount.com`) → quyền **Editor**.

2. **Thêm GitHub Secrets** (Settings → Secrets and variables → Actions):
   | Secret | Giá trị |
   |---|---|
   | `SITE_URL` | `https://nongsantuantuhanoi.vn/` |
   | `SHEET_ID` | `1huOAJ3z9og4Rah32rIvh4olqb1ue0R9vx6YuvCijGg4` |
   | `SHEET_TAB` | `Backlink Log` |
   | `GCP_SA_JSON` | *(dán nguyên nội dung JSON service account — giống index-bot)* |
   | `TELEGRAPH_TOKEN` | *(để TRỐNG lần đầu)* |
   | `MAX_NEW` | `8` |
   | `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | *(tuỳ chọn, muốn báo Telegram thì điền)* |

3. **Chạy tay lần đầu:** tab Actions → *Backlink Bot* → *Run workflow*.
   - Lần đầu `TELEGRAPH_TOKEN` trống → bot **tự tạo account** và **in token ra log** (dòng `LƯU token này...`).
   - Copy token đó → thêm vào Secret `TELEGRAPH_TOKEN` để các lần sau dùng chung 1 account (bài gom về 1 nơi).

Sau đó bot chạy **08:30 giờ VN mỗi ngày**, tự đẻ backlink cho mọi bài mới đăng và cập nhật Sheet.

## Chạy thử ở máy
```bash
pip install -r requirements.txt
export SITE_URL="https://nongsantuantuhanoi.vn/"
export SHEET_ID="1huOAJ3z9og4Rah32rIvh4olqb1ue0R9vx6YuvCijGg4"
export GCP_SA_JSON="$(cat service-account.json)"
python backlink_bot.py
```
