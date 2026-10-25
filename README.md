# Sentiment Analysis

Hệ thống phân tích tâm lý và dòng tiền thị trường crypto theo 5 tầng (thu thập → lọc → chấm điểm → tổng hợp → hiển thị), chạy miễn phí trên GitHub.
Trang: https://rintue.github.io/sentiment/ · Tài liệu đầy đủ: tệp PDF "Hướng dẫn chi tiết" trong gói sao lưu.

## Các phần
- `index.html`: trang web (tầng 5). Đọc dữ liệu trong `data/`; khi mở trang lấy thêm trực tiếp Bybit, OKX, Deribit, thanh lý theo thời gian thực (Bybit, OKX, Binance) và lịch Investing.com.
- `collector/collect.py`: thu thập (8 báo crypto, Reddit, Stocktwits, Bluesky, YouTube, 4 kênh tin Telegram, Mastodon, Lemmy, Hacker News), lọc, chấm điểm (Twitter-RoBERTa + từ điển crypto), chọn Tin nổi bật / Đám đông, dịch, số liệu vĩ mô; điều phối các phần dưới.
- `collector/market.py`: chỉ số tổng hợp 0–100, funding / open interest / thanh lý gộp 7 sàn (Coinalyze), Coinbase Premium, stablecoin, "Đà giá đến từ đâu" kèm kết luận tự động, bản đồ thanh lý cá voi Hyperliquid.
- `collector/attention.py`: Google Trends (toàn cầu, Việt Nam), hạng ứng dụng sàn trên App Store, Wikipedia nhiều trang.
- `collector/events.py`: lịch sự kiện vĩ mô và đo phản ứng giá BTC từ 2022.
- `collector/history_test.py`: kiểm chứng lịch sử các chỉ báo từ 2022, Điểm thuận lợi (học 2022–2024, kiểm tra 2025–nay).
- `collector/alerts.py`: cảnh báo Telegram (bản tin sáng, vùng cực đoan, funding bất thường, giá chạy mạnh, thanh lý dây chuyền, sự kiện vĩ mô cấp 1).
- `collector/cryptobert.py`: thử nghiệm so sánh mô hình; không ảnh hưởng điểm chính.
- `.github/workflows/collect.yml`: lịch chạy mỗi giờ (phút 17, giờ UTC).

Các tệp trong `data/` do máy chủ tự tạo, không cần sửa tay.

## Secrets (Settings → Secrets and variables → Actions)
- `COINALYZE_API_KEY`: gộp funding, open interest, thanh lý nhiều sàn (nên có).
- `FRED_API_KEY`: số liệu vĩ mô Mỹ đầy đủ hơn.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: bật cảnh báo Telegram.

Thiếu khóa nào thì phần đó dùng nguồn dự phòng hoặc bỏ qua, hệ thống vẫn chạy.

## Dựng lại từ đầu
1. Tạo kho **Public** (kho riêng tư chỉ có 2.000 phút Actions/tháng, không đủ chạy mỗi giờ).
2. Đưa toàn bộ tệp lên kho (kể cả `.github/workflows/collect.yml`).
3. Settings → Actions → General → Workflow permissions: **Read and write**.
4. Settings → Pages → Deploy from a branch → `main` / `(root)`.
5. Thêm Secrets ở trên; nhắn Start cho bot Telegram.
6. Actions → "Thu thập tâm lý crypto" → Run workflow.

Thông tin tham khảo, không phải lời khuyên đầu tư.
