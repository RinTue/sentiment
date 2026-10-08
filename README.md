# Sentiment Analysis Crypto

Hệ thống phân tích tâm lý thị trường crypto theo 5 tầng, chạy miễn phí trên GitHub.

- `collector/collect.py`: tầng 1–4. Mỗi giờ đọc 8 báo crypto, Reddit, Mastodon, Lemmy, Hacker News; lọc; chấm điểm bằng từ điển thuật ngữ crypto và mô hình Twitter RoBERTa; lưu kết quả vào `data/`.
- `.github/workflows/collect.yml`: lịch chạy mỗi giờ.
- `index.html`: tầng 5, trang web đọc `data/latest.json` và `data/history.csv`, kết hợp dữ liệu thị trường trực tiếp (Bybit, OKX, Deribit, SoSoValue, Fear & Greed, CoinGecko, Wikipedia, Investing.com) để viết nhận định.

Các tệp trong `data/` do máy chủ tự tạo, không cần sửa tay.

Thông tin tham khảo, không phải lời khuyên đầu tư.
