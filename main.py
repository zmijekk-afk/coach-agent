# (FULL FILE)

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


# ================= HELPERS =================

def build_summary_for_user(user_id):
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.now().date()

    cur.execute("""
        SELECT name, grams, calories
        FROM meals
        WHERE user_id=%s AND DATE(timestamp)=%s
    """, (user_id, today))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    if not rows:
        return "No meals logged today."

    total = 0
    lines = []

    for name, grams, kcal in rows:
        grams_text = f"{grams}g" if grams else "unknown weight"
        lines.append(f"- {name} (~{grams_text}, {kcal} kcal)")
        total += kcal

    lines.append(f"\nTotal: {total} kcal")

    return "\n".join(lines)


def set_user_name(user_id, new_name):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "UPDATE users SET name=%s WHERE id=%s",
        (new_name, user_id)
    )

    conn.commit()
    cur.close()
    conn.close()


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
Estimate portion size carefully.

Return JSON:
{
"name": "...",
"grams": number,
"calories": number,
"protein": number,
"carbs": number,
"fat": number
}
If unsure, estimate grams anyway.
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
    data = json.loads(clean_json(raw))

    # fallback if missing grams
    if not data.get("grams"):
        data["grams"] = 100

    return data


# ================= WEBHOOK =================

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.form()
    data = dict(data)

    phone = data.get("From")
    profile_name = data.get("ProfileName", "Unknown")

    user_id = get_or_create_user(phone, profile_name)

    num_media = int(data.get("NumMedia", 0))
    body = data.get("Body", "").lower().strip()

    # IMAGE
    if num_media > 0:
        image_url = data.get("MediaUrl0")

        try:
            est = estimate_calories(image_url)

            conn = get_conn()
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO meals (user_id, name, grams, calories, protein, carbs, fat, image_url, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id,
                est["name"],
                est["grams"],
                est["calories"],
                est["protein"],
                est["carbs"],
                est["fat"],
                image_url,
                datetime.now()
            ))

            conn.commit()
            cur.close()
            conn.close()

            reply = (
                f"{est['name']} (~{est['grams']}g, {est['calories']} kcal)\n"
                f"P: {est['protein']}g | C: {est['carbs']}g | F: {est['fat']}g"
            )

        except Exception as e:
            reply = f"ERROR: {str(e)}"

    # TEXT
    elif body:

        if body.startswith("name "):
            new_name = body.replace("name ", "").strip()
            set_user_name(user_id, new_name)
            reply = f"Name set to {new_name}"

        elif body == "me":
            reply = build_summary_for_user(user_id)

        elif body == "summary":
            reply = build_summary_for_user(user_id)

        else:
            reply = "Logged"

    else:
        reply = "Send photo or log"

    return Response(
        content=f"<Response><Message>{reply}</Message></Response>",
        media_type="application/xml"
    )
