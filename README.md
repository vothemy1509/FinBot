# FinBot MVP

FinBot là hệ thống báo cáo tài chính/crypto MVP chạy bằng Flask.

## Tính năng MVP
- Lấy dữ liệu crypto từ CoinGecko
- Lấy Fear & Greed Index
- Tính EMA, RSI, Bollinger Bands đơn giản
- Sinh báo cáo Markdown
- Dashboard web
- Gửi Telegram / Email nếu đã cấu hình ENV
- Healthcheck endpoint: `/health`

## Chạy local
```bash
pip install -r requirements.txt
python app.py
```

Mở: `http://127.0.0.1:5000`

## ENV
Sao chép `.env.example` thành `.env` rồi điền thông tin.

## Deploy Railway
1. Push code lên GitHub
2. Tạo project Railway từ repo
3. Khai báo ENV theo `.env.example`
4. Railway tự dùng `Procfile`: `web: gunicorn app:app`
5. Test `/health` sau deploy

## Gợi ý ENV tối thiểu
- `SECRET_KEY`
- `DATABASE_URL` (có thể để SQLite local)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (nếu dùng Telegram)
- `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` (nếu dùng email)
