# Prompt cho Claude: dựng lại và tiếp tục phát triển hệ thống Sentiment Analysis

Cách dùng: mở Claude (bản có Claude Code hoặc có quyền làm việc với GitHub), đính kèm tệp zip sao lưu, rồi dán **Prompt 1** nếu cần dựng lại từ đầu, hoặc **Prompt 2** nếu hệ thống vẫn chạy và chỉ muốn Claude tiếp tục sửa hoặc thêm tính năng. Phần "Bối cảnh cho Claude" ở cuối tệp này là để Claude tự đọc; không cần chỉnh.

Không bao giờ dán mã bot Telegram, khoá API hay mật khẩu vào khung chat. Các mã đó chỉ nằm trong GitHub → Settings → Secrets.

---

## Prompt 1: dựng lại từ đầu trên máy hoặc tài khoản mới

```
Tôi không biết lập trình. Tôi gửi kèm tệp sao lưu hệ thống "Sentiment Analysis" (trang theo dõi tâm lý và dòng tiền crypto chạy miễn phí trên GitHub Actions + GitHub Pages, có cảnh báo Telegram).

Hãy đọc PROMPT_CLAUDE.md (phần "Bối cảnh cho Claude") và README.md trong tệp zip trước, rồi giúp tôi dựng lại:
1. Tạo kho GitHub CÔNG KHAI (public) tên "sentiment" trong tài khoản GitHub của tôi: <điền tên tài khoản GitHub>.
2. Đưa toàn bộ nội dung trong thư mục sentiment/ của tệp zip lên nhánh main, kể cả thư mục ẩn .github/workflows và thư mục data/ (để giữ lịch sử).
3. Bật quyền ghi cho Actions (Settings → Actions → General → Workflow permissions: Read and write) và bật GitHub Pages (Deploy from a branch → main → /root). Việc nào bạn không tự làm được thì chỉ tôi bấm từng bước.
4. Chỉ tôi tự thêm 4 Secrets: COINALYZE_API_KEY, FRED_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Tuyệt đối không bảo tôi dán các mã này vào khung chat.
5. Chạy thử workflow "Thu thập tâm lý crypto" một lần, chờ chạy xong, kiểm tra trang https://<tài khoản>.github.io/sentiment/ hiển thị đủ các tab và Telegram nhận tin chào.
6. Báo cho tôi bằng tiếng Việt, ngắn gọn: cái gì đã chạy, cái gì còn thiếu và tôi cần bấm gì.
Trước khi tạo bất kỳ workflow thử nghiệm nào (có thể gửi email báo lỗi), hãy hỏi tôi.
```

## Prompt 2: tiếp tục sửa hoặc thêm tính năng

```
Tôi không biết lập trình. Hệ thống "Sentiment Analysis" của tôi ở kho GitHub <tài khoản>/sentiment, trang https://<tài khoản>.github.io/sentiment/.
Hãy đọc PROMPT_CLAUDE.md (phần "Bối cảnh cho Claude") trong kho hoặc tệp zip tôi gửi trước khi làm. Trả lời tôi bằng tiếng Việt.
Việc tôi muốn: <mô tả việc cần làm, kèm ảnh chụp nếu có>.
Khi đề xuất chỉ báo mới, hãy nói rõ nó có thật sự báo trước giá hay không (kiểm chứng trên dữ liệu chưa dùng để học), đừng chỉ làm cho đẹp.
```

---

## Bối cảnh cho Claude

### Người dùng
- Người Việt (giờ Asia/Saigon), trader crypto cá nhân, **không biết lập trình**. Luôn trả lời bằng tiếng Việt, ngắn, rõ, không thuật ngữ khi không cần.
- Thường gửi ảnh chụp từ trang khác (CoinGlass, Hyperliquid, Glassnode, các trang chu kỳ) và hỏi "có làm được không": hãy phân tích nguồn và giới hạn trước, nói thẳng điểm yếu, rồi hỏi có làm không. Khi họ nói "ok làm" thì làm trọn, tự kiểm tra, rồi báo kết quả.
- Muốn trình bày giống mẫu họ gửi; khi số liệu lệch mẫu phải giải thích vì sao và sửa cho khớp nếu có thể.

### Quy tắc bắt buộc
- **Không bao giờ** xin hay nhận mã bot, khoá API, mật khẩu qua chat. Secrets chỉ nằm trong GitHub Secrets. Không in secrets ra log (kho công khai, log công khai).
- Kho phải **public** (Actions miễn phí không giới hạn phút; kho private chỉ có 2.000 phút/tháng, không đủ chạy mỗi giờ).
- **Hỏi trước** khi tạo workflow thử nghiệm/probe: lần trước một workflow thử nghiệm gây email báo lỗi liên tục.
- Không đưa khoá sàn hay ví vào hệ thống. Đây là công cụ tham khảo, không giao dịch.
- Trung thực về khả năng dự báo: chỉ báo nào không qua kiểm tra ngoài mẫu (học 2022–2024, kiểm tra 2025–nay) thì ghi rõ. "Điểm thuận lợi" cũ đã thất bại kiểm tra và được thay bằng "La bàn thị trường" (mô tả trạng thái, không dự báo).
- Nguồn nào lỗi thì phần đó ẩn hoặc dùng bản đã lưu; lần chạy vẫn phải thành công (không làm đỏ Actions, không gây email lỗi).

### Kiến trúc
- `.github/workflows/collect.yml`: chạy `python collector/collect.py` theo lịch (phút 17, 37, 57 mỗi giờ, vì GitHub hay bỏ lượt chạy theo lịch) và khi bấm tay; `concurrency: collect`; xong thì commit thư mục `data/`. Secrets: `FRED_API_KEY`, `COINALYZE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (đều không bắt buộc).
- `index.html`: toàn bộ trang (một tệp, 2 khối `<script>`), đọc `data/*.json` và gọi thêm API công khai từ trình duyệt (Bybit, OKX, Deribit, Binance, Hyperliquid, Investing). Biểu đồ: SVG tự vẽ (`lineChart`) và ECharts 5.5.0 từ cdnjs (`loadEcharts()`). Lưu tuỳ chọn người xem trong localStorage với tiền tố `tamly-`.
- Các tab (từ 20/11/2026): Tổng quan = "Bối cảnh thị trường" bổ trợ phân tích kỹ thuật (2 kết luận 1–3 tháng / 1–2 năm, 4 thẻ Vĩ mô · Cá mập · Định giá · Tâm lý đám đông theo pha Dow, 3 dấu hiệu đổi chiều, phần "Xem chi tiết" đóng sẵn), Cá mập & phái sinh (ETF, thanh lý, cá voi Hyperliquid, đà giá), Vĩ mô & lịch (gộp phản ứng BTC với số liệu vĩ mô), Dài hạn (định giá on-chain + chu kỳ halving), Tin tức. Cách tính mở bằng đường dẫn cuối trang (#method). Chỉ số tâm lý tổng hợp cũ, La bàn, chấm điểm văn bản và Google Trends vẫn chạy ngầm nhưng ẩn khỏi trang; Google Trends đã bỏ khỏi nghiên cứu vì nhìn trước tương lai. Người dùng không muốn đường trung bình giá (đó là phân tích kỹ thuật, họ tự làm).
  - Thêm 20/11 (tối): `collector/marketwide.py` (gọi trong collect.py, làm mới ~50 phút) ghi `data/marketwide.json`: toàn cảnh thị trường (CoinGecko → CoinPaprika → 39 altcoin OKX), lịch đáo hạn quyền chọn Deribit + CME, sự kiện nhập tay còn hạn, và "Tin lớn 48 giờ" (cụm tin ≥2 báo, nhãn hack/luật/ETF/vĩ mô/doanh nghiệp/thị trường, tính trong news_highlights → `NEWS_BIG`). Tab Cá mập có mục Toàn cảnh; tab Vĩ mô có Lịch crypto tự động và Tin lớn; Tổng quan có dòng tóm tắt; Telegram nhắc đáo hạn quý trước 2 ngày, đáo hạn tháng ≥3 tỷ $ trước 1 ngày.
  - Thêm 20/11 (chiều): `data/context_log.csv` nhật ký kết luận mỗi ngày (đối chiếu sau 3–6 tháng); cảnh báo nguồn ngừng cập nhật trên thẻ và trong Telegram; dòng lưu ý kết luận 1–3 tháng (Vĩ mô yếu đi từ 2023); `data/events_manual.json` sự kiện crypto nhập tay (hiện ở tab Vĩ mô & lịch, nhắc Telegram trước 3 ngày); mục "Dòng tiền giữa BTC và altcoin" (tab Cá mập, lấy từ phần Luân chuyển của compass); Google Trends tắt hẳn; workflow thu thập có tuỳ chọn "Gửi tin Telegram thử"; workflow Bối cảnh có lịch dự phòng 09:23.
  - `context.py` (thêm 20/11): khung "Dấu hiệu thoát hàng" 4 điều kiện (định giá ≥80, FOMO: F&G ≥75 hoặc funding hạng ≥85, cá mập ≤40 khi giá trong 15% quanh đỉnh 1 năm, vĩ mô ≤40 hoặc giảm ≥20 điểm/60 ngày); chỉ hiện ở pha Hồi phục/Hưng phấn; 0 giữ, 1 để ý, 2+ chốt dần; bỏ lỡ đỉnh 10/2025.
  - `context.py`: đọc `data/research_features.csv` + `data/cycles.json`, ghi `data/context.json`. Ngưỡng cố định ở đầu tệp (CHEAP 1,15; VOL_PANIC 60; FNG_PANIC 25; dấu hiệu Vĩ mô >40, Cá mập >60). Kiểm chứng (từ 2015): Vĩ mô dưới 20 chỉ ~10% số lần giá cao hơn sau 3 tháng; Định giá dưới 20 ~96% cao hơn sau 1 năm; Cá mập yếu.
- `collector/`:
  - `collect.py`: điều phối; thu thập tin và mạng xã hội, lọc, chấm điểm (Twitter-RoBERTa ONNX + từ điển crypto), dịch, vĩ mô (FRED), rồi gọi các phần dưới.
  - `market.py`: chỉ số tổng hợp 0–100, funding / OI / thanh lý gộp 7 sàn (Coinalyze), Coinbase Premium, stablecoin, "Đà giá đến từ đâu", cá voi Hyperliquid (`hl_top_accounts`, `hl_harvest`, `hl_whales`), ghi `data/btc_daily.csv`.
  - `etf.py`: dòng tiền ETF BTC từ TFTC (`tftc.io/bitcoin-etf-flows/data.json`, không cần mã, CC BY 4.0), ghi `data/etf.json`.
  - `cycles.py`: giá theo ngày từ 2011 (blockchain.com trước 2018 + `btc_daily.csv`), giá cao/thấp trong ngày (Bitstamp, dự phòng CryptoCompare), số khối; ghi `data/cycles.json`. Trang tự tìm đáy/đỉnh, tỷ lệ 49,4/50,6, dự phóng thời gian (halving = halving trước + 1.459 ngày) và 3 kịch bản giá.
  - `history_test.py`: chạy mỗi ngày một lần; kiểm chứng lịch sử từ 2022, `liq_ext`, Điểm thuận lợi (cũ) và gọi `compass.py`.
  - `compass.py`: La bàn thị trường (ý tưởng Market Compass của Glassnode bằng nguồn miễn phí): 4 nhóm chính Vĩ mô, Dòng tiền, Hành vi nhà đầu tư, Nền tảng on-chain và 3 mục đọc riêng Chu kỳ, Phái sinh, Luân chuyển. Chỉ báo bám danh sách Glassnode công bố, thay bằng nguồn miễn phí (FRED; BGeometrics bitcoin-data.com miễn phí 15 lượt/ngày: lượng cung theo tuổi coin, lãi/lỗ đã chốt, STH-MVRV trễ 7 ngày, lưu đệm; Coinalyze lực mua spot ròng; Coin Metrics Community: cung trên sàn, dòng ra/vào sàn, MVRV trễ ~1 tháng; blockchain.com; Deribit DVOL; rổ ~40 altcoin OKX lưu đệm ở `data/compass_cache.json`). Mỗi chỉ báo xếp hạng so với 3 năm trước; điểm tổng = trung bình 4 nhóm rồi xếp hạng so với lịch sử (như Glassnode). `data/glassnode_ref.json` chứa số Glassnode chép tay từ ảnh người dùng gửi để đo độ lệch; thêm dòng khi có ảnh mới. Tăng `COMPASS_V` khi đổi cách tính để buộc tính lại.
  - `attention.py`: Google Trends (toàn cầu, Việt Nam), hạng app sàn trên App Store, Wikipedia.
  - `events.py`: lịch sự kiện vĩ mô và phản ứng BTC.
  - `alerts.py`: Telegram (bản tin sáng ~7 giờ, vùng cực đoan, giá chạy mạnh, thanh lý dây chuyền, ETF bất thường, funding bất thường, sự kiện cấp 1, báo thiếu dữ liệu); trạng thái trong `data/alerts_state.json`.
  - `cryptobert.py`: thử nghiệm so sánh mô hình, không ảnh hưởng điểm chính.

### Giới hạn đã biết
- GitHub bỏ nhiều lượt chạy theo lịch (có ngày chỉ 3–4 lượt). Cách khắc phục: dịch vụ ngoài như cron-job.org gọi API `workflow_dispatch` (cần token GitHub chỉ quyền Actions của kho này).
- Máy chủ GitHub bị chặn: Farside (403), SEC (403), DefiLlama ETF (402), SoSoValue API cũ (404). ETH ETF chưa có nguồn miễn phí không cần mã; SoSoValue API mới cần mã miễn phí `SOSOVALUE_API_KEY`.
- On-chain miễn phí (BGeometrics) trễ khoảng 1 tháng nên không dùng; Glassnode Market Compass là gói trả phí, không có API.
- Đọc trực tiếp Hyperliquid từ trình duyệt chưa được kiểm thử với API thật; có dòng báo dự phòng.

### Cách làm việc khi sửa mã
- Sửa thẳng `index.html`; kiểm tra cú pháp từng khối script bằng `node -e "new Function(src)"`; Python bằng `python -m py_compile`.
- Xem thử bằng Playwright (Chromium), chặn mạng ngoài, chụp ở 1280 và 390 px (điện thoại) và tự xem ảnh trước khi báo xong.
- Commit message tiếng Việt, mô tả việc đã làm. Sau khi đẩy, có thể bấm chạy workflow để có dữ liệu thật rồi kiểm tra `data/latest.json`.
- Ý tưởng đang chờ người dùng quyết: chỉ báo "lệch pha spot – phái sinh" (khối lượng spot thấp, funding dương kéo dài), ETF ETH (cần mã SoSoValue), cron-job.org.

### Giao diện (từ 20/11/2026)
- Mặc định tông sáng ấm (kem giấy), nút ◐ chuyển tông tối dịu (xám than); lưu ở localStorage `tamly-theme2` ("warm"/"dark"). Các khung biểu đồ kiểu CoinGlass giữ nền xám than #2d333b ở cả hai tông. Màu định nghĩa trong khối CSS "Giao diện chuyên nghiệp" cuối `<style>`.
- Thanh giá cố định trên cùng `#tk` (BTC, vốn hoá, tỷ trọng BTC, F&G, funding, S&P, vàng, 10Y, USD) do `tickerRender()` vẽ; tab dính ngay dưới.
- Tổng quan: dòng "Hôm nay cần chú ý" → 2 xu hướng → 4 thẻ (có đường 90 ngày) → [Đảo chiều đi lên | Chốt lời] → [Thế giới 7 ngày | Lịch 14 ngày] → toàn thị trường crypto → Xem chi tiết.
- Cá mập: hàng KPI `#wk-kpi` ở đầu. Vĩ mô: bảng thế giới `#world-box` ở đầu. Tin tức: "Tin lớn 48 giờ" `#big-box2` ở đầu.
- Điện thoại: bảng rộng tự cuộn ngang trong thẻ, trang không tràn.
