from fastapi import FastAPI, Request
from fastapi.responses import Response
from datetime import datetime
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

    # USERS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        phone_number TEXT UNIQUE,
        created_at TIMESTAMP
    )
    """)

    # MEALS
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

    # LOGS (for counting activity)
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
        cur.execute(
            "INSERT INTO users (phone_number, created_at) VALUES (%s, %s) RETURNING id",
            (phone, datetime.now())
        )
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
        SELECT name, calories, protein, carbs, fat
        FROM meals
        WHERE user_id=%s AND DATE(timestamp)=%s
    """, (user_id, today))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "name": r[0],
            "calories": r[1],
            "protein": r[2],
            "carbs": r[3],
            "fat": r[4]
        }
        for r in rows
    ]


def get_today_totals(user_id):
    meals = load_today_meals(user_id)

    totals = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    for m in meals:
        totals["calories"] += m["calories"]
        totals["protein"] += m["protein"]
        totals["carbs"] += m["carbs"]
        totals["fat"] += m["fat"]

    return totals


# ---------------- QUERY ----------------
def answer_query(user_id):
    meals = load_today_meals(user_id)

    if not meals:
        return "No meals logged today."

    lines = []
    totals = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    for m in meals:
        lines.append(f"- {m['name']} (~{m['calories']} kcal)")
        totals["calories"] += m["calories"]
        totals["protein"] += m["protein"]
        totals["carbs"] += m["carbs"]
        totals["fat"] += m["fat"]

    lines.append(
        f"\nTotal: {totals['calories']} kcal\n"
        f"P: {totals['protein']}g | C: {totals['carbs']}g | F: {totals['fat']}g"
    )

    return "\n".join(lines)


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
                {"type": "input_image",
                 "image_url": data_url}
            ]
        }]
    )

    raw = response.output[0].content[0].text
    return json.loads(clean_json(raw))


# ---------------- REMINDER ----------------
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

    return {"status": f"sent to {len(users)} users"}


# ---------------- DAILY SUMMARY ----------------
def build_daily_summary():
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.now().date()

    cur.execute("SELECT id, phone_number FROM users")
    users = cur.fetchall()

    lines = ["📊 Daily summary:\n"]

    for user_id, phone in users:

        # Count logs
        cur.execute("""
            SELECT COUNT(*) FROM logs
            WHERE user_id=%s AND DATE(timestamp)=%s
        """, (user_id, today))

        count = cur.fetchone()[0]

        if count < 3:
            status = "pości lub nie dostarczył kompletnych danych"
        else:
            cur.execute("""
                SELECT COALESCE(SUM(calories), 0)
                FROM meals
                WHERE user_id=%s AND DATE(timestamp)=%s
            """, (user_id, today))

            calories = cur.fetchone()[0]
            status = f"{calories} kcal"

        lines.append(f"{phone}: {status}")

    cur.close()
    conn.close()

    return "\n".join(lines)


@app.get("/send-daily-summary")
def send_daily_summary():
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

            totals = get_today_totals(user_id)

            reply = (
                f"{est['name']} (~{est['calories']} kcal)\n"
                f"P: {est['protein']}g | C: {est['carbs']}g | F: {est['fat']}g\n\n"
                f"Today: {totals['calories']} kcal"
            )

        except Exception as e:
            reply = f"ERROR: {str(e)}"

    elif body:
        save_log(user_id, "text")
        reply = answer_query(user_id)

    else:
        reply = "Send photo or ask question"

    return Response(
        content=f"<Response><Message>{reply}</Message></Response>",
        media_type="application/xml"
    )
