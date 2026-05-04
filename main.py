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


# ================= DB =================
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        phone_number TEXT UNIQUE,
        name TEXT,
        streak INT DEFAULT 0,
        last_active DATE,
        created_at TIMESTAMP
    )
    """)

    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS streak INT DEFAULT 0;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active DATE;")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS meals (
        id SERIAL PRIMARY KEY,
        user_id INT,
        name TEXT,
        grams INT,
        calories INT,
        protein FLOAT,
        carbs FLOAT,
        fat FLOAT,
        image_url TEXT,
        timestamp TIMESTAMP
    )
    """)

    cur.execute("ALTER TABLE meals ADD COLUMN IF NOT EXISTS user_id INT;")
    cur.execute("ALTER TABLE meals ADD COLUMN IF NOT EXISTS grams INT;")

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


# ================= USERS =================
def get_or_create_user(phone, profile_name):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM users WHERE phone_number=%s", (phone,))
    row = cur.fetchone()

    if row:
        user_id, existing_name = row

        if profile_name and profile_name != existing_name:
            cur.execute(
                "UPDATE users SET name=%s WHERE id=%s",
                (profile_name, user_id)
            )
            conn.commit()
    else:
        cur.execute("""
            INSERT INTO users (phone_number, name, created_at)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (phone, profile_name, datetime.now()))
        user_id = cur.fetchone()[0]
        conn.commit()

    cur.close()
    conn.close()
    return user_id


# ================= SAVE =================
def save_meal(user_id, entry):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO meals (user_id, name, grams, calories, protein, carbs, fat, image_url, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        user_id,
        entry["name"],
        entry["grams"],
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


# ================= STREAK =================
def update_streak(user_id, eligible):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT streak, last_active FROM users WHERE id=%s", (user_id,))
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


# ================= AI =================
def clean_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json", "").strip()
    return text


def estimate_calories(image_url):
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")

    res = requests.get(image_url, auth=(sid, token))
    if res.status_code != 200:
        raise Exception(f"Image download failed: {res.status_code}")

    img64 = base64.b64encode(res.content).decode()
    data_url = f"data:image/jpeg;base64,{img64}"

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": """
Identify the meal and estimate portion size.

Return JSON:
{
"name": "...",
"grams": number,
"calories": number,
"protein": number,
"carbs": number,
"fat": number
}
"""
                },
                {
                    "type": "input_image",
                    "image_url": data_url
                }
            ]
        }]
    )

    raw = response.output[0].content[0].text
    return json.loads(clean_json(raw))


# ================= WEBHOOK =================
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.form()
    data = dict(data)

    phone = data.get("From")
    profile_name = data.get("ProfileName", "Unknown")

    user_id = get_or_create_user(phone, profile_name)

    num_media = int(data.get("NumMedia", 0))
    body = data.get("Body", "").lower()

    if num_media > 0:
        image_url = data.get("MediaUrl0")

        try:
            est = estimate_calories(image_url)

            save_meal(user_id, {
                "name": est["name"],
                "grams": est["grams"],
                "calories": est["calories"],
                "protein": est["protein"],
                "carbs": est["carbs"],
                "fat": est["fat"],
                "image_url": image_url,
                "timestamp": datetime.now()
            })

            save_log(user_id, "meal")

            reply = (
                f"{est['name']} (~{est['grams']}g, {est['calories']} kcal)\n"
                f"P: {est['protein']}g | "
                f"C: {est['carbs']}g | "
                f"F: {est['fat']}g"
            )

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
