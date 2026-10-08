# Sentiment Analysis

Hệ thống phân tích tâm lý thị trường crypto theo 5 tầng, chạy miễn phí trên GitHub.

- `collector/collect.py`: tầng 1–4. Mỗi giờ đọc 8 báo crypto, Reddit, Mastodon, Lemmy, Hacker News; lọc; chấm điểm bằng từ điển thuật ngữ crypto và mô hình Twitter RoBERTa; lưu kết quả vào `data/`.
- `.github/workflows/collect.yml`: lịch chạy mỗi giờ.
- `index.html`: tầng 5, trang web đọc `data/latest.json` và `data/history.csv`, kết hợp dữ liệu thị trường trực tiếp (Bybit, OKX, Deribit, SoSoValue, Fear & Greed, CoinGecko, Wikipedia, Investing.com) để viết nhận định.

Các tệp trong `data/` do máy chủ tự tạo, không cần sửa tay.

Phần vĩ mô lấy từ FRED; khi FRED không trả lời, bộ thu thập dùng Bộ Tài chính Mỹ, Fed New York và Yahoo Finance, rồi đến bản lưu gần nhất (`data/macro_cache.json`). Không bắt buộc: thêm khóa miễn phí của FRED vào Settings → Secrets and variables → Actions với tên `FRED_API_KEY` để lấy thêm bảng cân đối Fed, cung tiền M2 và chênh lệch tín dụng.

Thông tin tham khảo, không phải lời khuyên đầu tư.
