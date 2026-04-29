from fastapi import FastAPI, Request
from fastapi.responses import Response
from datetime import datetime, timedelta
import os
import json
import base64
import requests
import psycopg2
from openai import OpenAI

app = FastAPI()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
DATABASE_URL = os.getenv("DATABASE_URL")


# ---------------- DB ----------------
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        phone_number TEXT UNIQUE,
        streak INT DEFAULT 0,
        last_active DATE,
        created_at TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS meals (
        id SERIAL PRIMARY KEY,
        user_id INT,
        name TEXT,
        calories INT,
        protein FLOAT,
        carbs FLOAT,
        fat FLOAT,
        image_url TEXT,
        timestamp TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id SERIAL PRIMARY KEY,
        user_id INT,
        type TEXT,
        timestamp TIMESTAMP
    )
    """)

    conn.commit()
    cur.close()
    conn.close()


init_db()


# ---------------- USER ----------------
def get_or_create_user(phone):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE phone_number=%s", (phone,))
    row = cur.fetchone()

    if row:
        user_id = row[0]
    else:
        cur.execute("""
            INSERT INTO users (phone_number, created_at)
            VALUES (%s, %s) RETURNING id
        """, (phone, datetime.now()))
        user_id = cur.fetchone()[0]
        conn.commit()

    cur.close()
    conn.close()
    return user_id


# ---------------- SAVE ----------------
def save_meal(user_id, entry):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO meals (user_id, name, calories, protein, carbs, fat, image_url, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        user_id,
        entry["name"],
        entry["calories"],
        entry["protein"],
        entry["carbs"],
        entry["fat"],
        entry["image_url"],
        entry["timestamp"]
    ))

    conn.commit()
    cur.close()
    conn.close()


def save_log(user_id, log_type):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO logs (user_id, type, timestamp)
        VALUES (%s, %s, %s)
    """, (user_id, log_type, datetime.now()))

    conn.commit()
    cur.close()
    conn.close()


# ---------------- LOAD ----------------
def load_today_meals(user_id):
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.now().date()

    cur.execute("""
        SELECT name, calories FROM meals
        WHERE user_id=%s AND DATE(timestamp)=%s
    """, (user_id, today))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows


def count_logs_today(user_id):
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.now().date()

    cur.execute("""
        SELECT COUNT(*) FROM logs
        WHERE user_id=%s AND DATE(timestamp)=%s
    """, (user_id, today))

    count = cur.fetchone()[0]

    cur.close()
    conn.close()

    return count


def sum_calories_today(user_id):
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.now().date()

    cur.execute("""
        SELECT COALESCE(SUM(calories),0)
        FROM meals
        WHERE user_id=%s AND DATE(timestamp)=%s
    """, (user_id, today))

    total = cur.fetchone()[0]

    cur.close()
    conn.close()

    return total


# ---------------- STREAK ----------------
def update_streak(user_id, eligible):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT streak, last_active FROM users WHERE id=%s
    """, (user_id,))
    row = cur.fetchone()

    streak, last_active = row if row else (0, None)

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    if eligible:
        if last_active == yesterday:
            streak += 1
        elif last_active != today:
            streak = 1
    else:
        streak = 0

    cur.execute("""
        UPDATE users SET streak=%s, last_active=%s WHERE id=%s
    """, (streak, today, user_id))

    conn.commit()
    cur.close()
    conn.close()

    return streak


# ---------------- AI ----------------
def clean_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json", "").strip()
    return text


def estimate_calories(image_url):
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")

    res = requests.get(image_url, auth=(sid, token))
    img64 = base64.b64encode(res.content).decode()

    data_url = f"data:image/jpeg;base64,{img64}"

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": """
Identify food + estimate nutrition.

Return JSON:
{
"name": "...",
"calories": number,
"protein": number,
"carbs": number,
"fat": number
}
"""},
                {"type": "input_image", "image_url": data_url}
            ]
        }]
    )

    raw = response.output[0].content[0].text
    return json.loads(clean_json(raw))


# ---------------- SUMMARY ----------------
def build_daily_summary():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, phone_number FROM users")
    users = cur.fetchall()

    ranked = []
    disqualified = []

    for user_id, phone in users:
        logs = count_logs_today(user_id)

        if logs < 3:
            update_streak(user_id, False)
            disqualified.append(phone)
        else:
            score = logs * 10
            streak = update_streak(user_id, True)

            ranked.append({
                "phone": phone,
                "logs": logs,
                "score": score,
                "streak": streak
            })

    ranked.sort(key=lambda x: x["score"], reverse=True)

    lines = ["🏆 Daily discipline ranking:\n"]

    medals = ["🥇", "🥈", "🥉"]

    for i, u in enumerate(ranked):
        medal = medals[i] if i < 3 else "•"
        lines.append(
            f"{medal} {u['phone']} — {u['logs']} logs | 🔥 {u['streak']} | {u['score']} pts"
        )

    if disqualified:
        lines.append("\n❌ Disqualified:")
        for p in disqualified:
            lines.append(f"{p} — pości lub nie dostarczył kompletnych danych")

    cur.close()
    conn.close()

    return "\n".join(lines)


# ---------------- REMINDERS ----------------
@app.get("/send-reminder")
def send_reminder():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT phone_number FROM users")
    users = cur.fetchall()

    cur.close()
    conn.close()

    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")

    for (phone,) in users:
        requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data={
                "From": "whatsapp:+14155238886",
                "To": phone,
                "Body": "Siema byczq, pamiętaj o foteczkach 📸"
            },
            auth=(sid, token)
        )

    return {"status": "reminder sent"}


@app.get("/send-daily-summary")
def send_summary():
    summary = build_daily_summary()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT phone_number FROM users")
    users = cur.fetchall()

    cur.close()
    conn.close()

    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")

    for (phone,) in users:
        requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data={
                "From": "whatsapp:+14155238886",
                "To": phone,
                "Body": summary
            },
            auth=(sid, token)
        )

    return {"status": "summary sent"}


# ---------------- WEBHOOK ----------------
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.form()
    data = dict(data)

    phone = data.get("From")
    user_id = get_or_create_user(phone)

    num_media = int(data.get("NumMedia", 0))
    body = data.get("Body", "").lower()

    if num_media > 0:
        image_url = data.get("MediaUrl0")

        try:
            est = estimate_calories(image_url)

            save_meal(user_id, {
                "name": est["name"],
                "calories": est["calories"],
                "protein": est["protein"],
                "carbs": est["carbs"],
                "fat": est["fat"],
                "image_url": image_url,
                "timestamp": datetime.now()
            })

            save_log(user_id, "meal")

            reply = f"{est['name']} (~{est['calories']} kcal)"

        except Exception as e:
            reply = f"ERROR: {str(e)}"

    elif body:
        save_log(user_id, "text")
        reply = "Logged"

    else:
        reply = "Send photo or log"

    return Response(
        content=f"<Response><Message>{reply}</Message></Response>",
        media_type="application/xml"
    )
