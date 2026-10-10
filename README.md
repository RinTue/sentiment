# Sentiment Analysis

Hệ thống phân tích tâm lý thị trường crypto theo 5 tầng, chạy miễn phí trên GitHub. Trang: https://rintue.github.io/sentiment/

## Các phần
- `collector/collect.py`: tầng 1–3 và vĩ mô. Mỗi giờ đọc 8 báo crypto, Reddit, 11 kênh YouTube, 4 kênh tin Telegram, Mastodon, Lemmy, Hacker News; lọc; chấm điểm bằng mô hình Twitter RoBERTa kết hợp từ điển thuật ngữ crypto; dịch bài tiêu biểu sang tiếng Việt; lấy số liệu vĩ mô (FRED, dự phòng: Bộ Tài chính Mỹ, Fed New York, Yahoo).
- `collector/market.py`: tầng 4 trên máy chủ. Chỉ số tổng hợp 0–100 mỗi giờ (`data/composite.csv`), Coinbase Premium, stablecoin, kiểm chứng Fear & Greed từ 2018.
- `collector/alerts.py`: cảnh báo Telegram (bản tin buổi sáng, vùng cực đoan, funding bất thường, sự kiện vĩ mô cấp 1).
- `collector/cryptobert.py`: thử nghiệm chấm song song bằng CryptoBERT để so sánh; không ảnh hưởng điểm chính.
- `.github/workflows/collect.yml`: lịch chạy mỗi giờ (phút 17).
- `index.html`: tầng 5, trang web đọc dữ liệu trong `data/` và lấy thêm số liệu trực tiếp (Bybit, OKX, Deribit, Investing.com).

Các tệp trong `data/` do máy chủ tự tạo, không cần sửa tay.

## Secrets (Settings → Secrets and variables → Actions), đều không bắt buộc
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: bật cảnh báo Telegram.
- `FRED_API_KEY`: khóa miễn phí của FRED, để lấy thêm bảng cân đối Fed, cung tiền M2 và chênh lệch tín dụng.

Giữ kho ở chế độ công khai: kho riêng tư chỉ có 2.000 phút GitHub Actions miễn phí mỗi tháng, không đủ cho lịch chạy mỗi giờ.

Thông tin tham khảo, không phải lời khuyên đầu tư.
