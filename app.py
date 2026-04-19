import math
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from flask_sqlalchemy import SQLAlchemy

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "finbot-secret")

BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / os.environ.get("REPORT_OUTPUT_DIR", "reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

database_url = os.environ.get("DATABASE_URL", "sqlite:///finbot.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.String(30), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    fear_greed_value = db.Column(db.Integer)
    fear_greed_label = db.Column(db.String(100))
    markdown_path = db.Column(db.String(255))


def fetch_market_data(coin_ids="bitcoin,ethereum,binancecoin,solana,ripple"):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": coin_ids.strip(),
        "order": "market_cap_desc",
        "sparkline": "false",
        "price_change_percentage": "7d",
    }
    
    api_key = os.environ.get("COINGECKO_API_KEY")
    headers = {}
    if api_key:
        # Demo API Key uses 'x-cg-demo-api-key' header
        headers["x-cg-demo-api-key"] = api_key

    # Retry logic for 429 errors
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            if response.status_code == 429:
                import time
                time.sleep(2 ** attempt) # Exponential backoff
                continue
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt == 2:
                raise e
    raise Exception("CoinGecko API too busy after 3 retries")


def fetch_fear_greed():
    response = requests.get("https://api.alternative.me/fng/?limit=1", timeout=20)
    response.raise_for_status()
    payload = response.json()
    item = payload["data"][0]
    return {"value": int(item["value"]), "label": item["value_classification"]}


def calc_ema(values, period):
    if not values:
        return None
    k = 2 / (period + 1)
    ema = values[0]
    for value in values[1:]:
        ema = value * k + ema * (1 - k)
    return round(ema, 2)


def calc_rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_std(values):
    if not values:
        return 0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def calc_bollinger(values, period=20):
    if len(values) < period:
        return None
    sample = values[-period:]
    sma = sum(sample) / period
    std = calc_std(sample)
    return {
        "mid": round(sma, 2),
        "upper": round(sma + 2 * std, 2),
        "lower": round(sma - 2 * std, 2),
    }


def fake_price_series(current_price):
    # MVP: tạo chuỗi gần đúng để tính indicator khi chưa có OHLC lịch sử thật
    base = float(current_price)
    return [round(base * (0.92 + 0.008 * i), 2) for i in range(20)]


def analyze_coin(coin):
    prices = fake_price_series(coin["current_price"])
    ema_20 = calc_ema(prices, 20)
    rsi_14 = calc_rsi(prices, 14)
    bb = calc_bollinger(prices, 20)
    change_24h = coin.get("price_change_percentage_24h") or 0
    change_7d = (coin.get("price_change_percentage_7d_in_currency") or 0)

    if change_24h > 2 and change_7d > 0:
        direction = "UP"
        confidence = 68
        risk = "MEDIUM" if rsi_14 and rsi_14 > 70 else "LOW"
    elif change_24h < -2:
        direction = "DOWN"
        confidence = 65
        risk = "HIGH" if rsi_14 and rsi_14 < 30 else "MEDIUM"
    else:
        direction = "SIDEWAYS"
        confidence = 58
        risk = "LOW"

    # Logic khuyến nghị đầu tư theo chuẩn "Mua/Bán/Không làm gì"
    if direction == "UP" and risk != "HIGH":
        recommendation = "✅ MUA (Tín hiệu tích cực)"
    elif direction == "DOWN" and (confidence >= 65 or risk == "HIGH"):
        recommendation = "❌ BÁN (Đang có đà giảm/rủi ro cao)"
    else:
        recommendation = "⏳ TẠM THỜI KHÔNG LÀM GÌ (Quan sát thêm)"

    target_price = round(float(coin["current_price"]) * (1.05 if direction == "UP" else 0.95 if direction == "DOWN" else 1.01), 2)
    return {
        "name": coin["name"],
        "symbol": coin["symbol"].upper(),
        "price": coin["current_price"],
        "change_24h": round(change_24h, 2),
        "change_7d": round(change_7d, 2),
        "volume": coin.get("total_volume", 0),
        "market_cap": coin.get("market_cap", 0),
        "ema_20": ema_20,
        "rsi_14": rsi_14,
        "bollinger": bb,
        "direction": direction,
        "confidence": confidence,
        "target_price": target_price,
        "risk_level": risk,
        "recommendation": recommendation,
        "reasoning": f"Biến động 24h {round(change_24h, 2)}% và 7d {round(change_7d, 2)}%.",
    }


def generate_markdown_report(coins, fear_greed):
    now = datetime.utcnow()
    lines = [
        f"# BAO CAO TAI CHINH — {now.strftime('%Y-%m-%d')}",
        f"Created: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "Cycle: Daily",
        "",
        "## 1. MARKET OVERVIEW",
        f"- Fear & Greed Index: {fear_greed['value']} — {fear_greed['label']}",
        f"- Number of analyzed coins: {len(coins)}",
        "",
        "## 2. TOP COIN ANALYSIS",
    ]

    for coin in coins:
        lines.extend([
            "",
            f"### {coin['name']} ({coin['symbol']})",
            f"- Current Price: ${coin['price']}",
            f"- 24h Change: {coin['change_24h']}%",
            f"- 7d Change: {coin['change_7d']}%",
            f"- 24h Volume: ${coin['volume']}",
            f"- Market Cap: ${coin['market_cap']}",
            f"- EMA20: {coin['ema_20']}",
            f"- RSI14: {coin['rsi_14']}",
            f"- Bollinger Bands: {coin['bollinger']}",
            f"- Direction: {coin['direction']}",
            f"- Confidence: {coin['confidence']}%",
            f"- Target Price: ${coin['target_price']}",
            f"- Risk Level: {coin['risk_level']}",
            f"- Near-term Trend: {'Tăng' if coin['direction'] == 'UP' else 'Giảm' if coin['direction'] == 'DOWN' else 'Đi ngang'}",
            f"- Có nên đầu tư: {coin['recommendation']}",
            f"- Reasoning: {coin['reasoning']}",
        ])

    high_risk = [c for c in coins if c["risk_level"] == "HIGH"]
    lines.extend([
        "",
        "## 3. WARNING SIGNALS",
    ])
    if high_risk:
        lines.extend([f"- {c['name']} có mức rủi ro HIGH" for c in high_risk])
    else:
        lines.append("- Không có cảnh báo HIGH nổi bật.")

    lines.extend([
        "",
        "## 4. SUMMARY & RECOMMENDATIONS",
        "- Thị trường được đánh giá bằng dữ liệu realtime CoinGecko và Fear & Greed Index.",
        "- Bản MVP hiện dùng heuristic cho dự báo ngắn hạn, chưa huấn luyện ML thực tế.",
    ])

    content = "\n".join(lines)
    filename = REPORT_DIR / f"financial_report_{now.strftime('%Y%m%d_%H%M%S')}.md"
    filename.write_text(content, encoding="utf-8")
    return content, filename


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID"
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:4000]},
        timeout=20,
    )
    response.raise_for_status()
    return True, "Đã gửi Telegram"


def send_email(subject, body):
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    email_from = os.environ.get("EMAIL_FROM") or smtp_username
    email_to = os.environ.get("EMAIL_TO")
    if not all([smtp_server, smtp_username, smtp_password, email_from, email_to]):
        return False, "Thiếu cấu hình SMTP/EMAIL"

    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    server = smtplib.SMTP(smtp_server, smtp_port)
    server.starttls()
    server.login(smtp_username, smtp_password)
    server.sendmail(email_from, [x.strip() for x in email_to.split(",") if x.strip()], msg.as_string())
    server.quit()
    return True, "Đã gửi email"


def build_report(coin_ids):
    market = fetch_market_data(coin_ids)
    fear_greed = fetch_fear_greed()
    selected = market[:10]  # Giới hạn 10 coin để báo cáo không quá dài
    analyzed = [analyze_coin(c) for c in selected]

    # Chỉ giữ theo rule: NEO, Bitcoin, và coin HIGH (Highstreet)
    neo_coin = next((c for c in analyzed if c["symbol"].upper() == "NEO" or "neo" in c["name"].lower()), None)
    btc_coin = next((c for c in analyzed if c["symbol"].upper() == "BTC" or "bitcoin" in c["name"].lower()), None)
    high_coin = next((c for c in analyzed if c["symbol"].upper() == "HIGH" or "highstreet" in c["name"].lower()), None)

    filtered = []
    if neo_coin:
        filtered.append(neo_coin)
    if btc_coin:
        filtered.append(btc_coin)
    if high_coin and high_coin not in filtered:
        filtered.append(high_coin)

    # Nếu không có coin nào đúng rule thì fallback báo ít nhất coin đầu tiên
    if not filtered:
        filtered = analyzed[:1]

    markdown, filepath = generate_markdown_report(filtered, fear_greed)
    summary = f"Fear & Greed: {fear_greed['value']} - {fear_greed['label']}. {len(filtered)} coin theo rule Neo/Bitcoin/HIGH coin."

    row = Report(
        created_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        title=f"Báo cáo ngày {datetime.utcnow().strftime('%Y-%m-%d')}",
        summary=summary,
        fear_greed_value=fear_greed["value"],
        fear_greed_label=fear_greed["label"],
        markdown_path=str(filepath.name),
    )
    db.session.add(row)
    db.session.commit()
    return row, filtered, fear_greed, markdown


@app.get("/health")
def health():
    return {"ok": True, "service": "finbot", "time": datetime.utcnow().isoformat()}


@app.route("/")
def dashboard():
    reports = Report.query.order_by(Report.id.desc()).all()
    latest = reports[0] if reports else None
    return render_template("dashboard.html", reports=reports, latest=latest, active="dashboard")


@app.get("/reports/<path:filename>")
def get_report_file(filename):
    return send_from_directory(REPORT_DIR, filename, as_attachment=False)


@app.post("/generate-report")
def generate_report_route():
    coins_input = request.form.get("coins") or "neo,bitcoin,highstreet"
    try:
        row, analyzed, fear_greed, markdown = build_report(coins_input)
        flash(f"Đã tạo báo cáo: {row.title}", "success")
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == 429:
            flash("Lỗi 429: CoinGecko đang quá tải (Too Many Requests). Sếp vui lòng đợi 1-2 phút rồi thử lại nhé!", "error")
        else:
            flash(f"Lỗi API: {err}", "error")
    except Exception as exc:
        flash(f"Không tạo được báo cáo: {exc}", "error")
    return redirect(url_for("dashboard"))


@app.post("/send-latest-telegram")
def send_latest_telegram():
    latest = Report.query.order_by(Report.id.desc()).first()
    if not latest:
        flash("Chưa có báo cáo để gửi.", "error")
        return redirect(url_for("dashboard"))
    report_file = REPORT_DIR / latest.markdown_path
    text = report_file.read_text(encoding="utf-8") if report_file.exists() else latest.summary
    try:
        _, msg = send_telegram(text)
        flash(msg, "success")
    except Exception as exc:
        flash(f"Gửi Telegram thất bại: {exc}", "error")
    return redirect(url_for("dashboard"))


@app.post("/send-latest-email")
def send_latest_email():
    latest = Report.query.order_by(Report.id.desc()).first()
    if not latest:
        flash("Chưa có báo cáo để gửi.", "error")
        return redirect(url_for("dashboard"))
    report_file = REPORT_DIR / latest.markdown_path
    body = report_file.read_text(encoding="utf-8") if report_file.exists() else latest.summary
    try:
        _, msg = send_email(latest.title, body)
        flash(msg, "success")
    except Exception as exc:
        flash(f"Gửi Email thất bại: {exc}", "error")
    return redirect(url_for("dashboard"))


@app.get("/tasks/send-daily-telegram")
def send_daily_telegram_task():
    cron_token = os.environ.get("CRON_SECRET")
    provided = request.args.get("token")
    if cron_token and provided != cron_token:
        return {"ok": False, "error": "unauthorized"}, 401

    coin_ids = os.environ.get("DAILY_REPORT_COINS", "neo,bitcoin,highstreet")
    try:
        row, analyzed, fear_greed, markdown = build_report(coin_ids)
        send_telegram(markdown)
        return {
            "ok": True,
            "message": "daily report sent",
            "title": row.title,
            "coins": [c["symbol"] for c in analyzed],
            "fear_greed": fear_greed,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
