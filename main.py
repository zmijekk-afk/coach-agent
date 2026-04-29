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


# ---- DB CONNECTION ----
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# ---- INIT TABLE ----
def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS meals (
        id SERIAL PRIMARY KEY,
        name TEXT,
        calories INT,
        protein FLOAT,
        carbs FLOAT,
        fat FLOAT,
        image_url TEXT,
        timestamp TIMESTAMP
    )
    """)

    conn.commit()
    cur.close()
    conn.close()


init_db()


@app.get("/")
def home():
    return {"status": "running"}


# =====================================================
# 🔥 NEW: REMINDER ENDPOINT (CALLED BY CRON)
# =====================================================
@app.get("/send-reminder")
def send_reminder():
    ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json"

    data = {
        "From": "whatsapp:+14155238886",
        "To": "whatsapp:+48533913613",  # your number
        "Body": "Siema byczq, pamiętaj o foteczkach 📸"
    }

    requests.post(url, data=data, auth=(ACCOUNT_SID, AUTH_TOKEN))

    return {"status": "reminder sent"}


# ---- SAVE MEAL ----
def save_meal(entry):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO meals (name, calories, protein, carbs, fat, image_url, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
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


# ---- LOAD TODAY ----
def load_today_meals():
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.now().date()

    cur.execute("""
        SELECT name, calories, protein, carbs, fat, timestamp
        FROM meals
        WHERE DATE(timestamp) = %s
    """, (today,))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "name": r[0],
            "calories": r[1],
            "protein": r[2],
            "carbs": r[3],
            "fat": r[4],
            "timestamp": r[5].isoformat()
        }
        for r in rows
    ]


# ---- TOTALS ----
def get_today_totals():
    meals = load_today_meals()

    totals = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    for m in meals:
        totals["calories"] += m["calories"]
        totals["protein"] += m["protein"]
        totals["carbs"] += m["carbs"]
        totals["fat"] += m["fat"]

    return totals


# ---- AI: detect question ----
def is_question(text):
    response = client.responses.create(
        model="gpt-4o-mini",
        input=f"""
Message: "{text}"

Does this ask about past meals, calories, or activity?

Answer only:
yes
or
no
"""
    )
    return "yes" in response.output_text.lower()


# ---- ANSWER QUERY ----
def answer_query(user_text):
    meals = load_today_meals()

    if not meals:
        return "No meals logged today."

    lines = []
    total = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    for m in meals:
        lines.append(f"- {m['name']} (~{m['calories']} kcal)")
        total["calories"] += m["calories"]
        total["protein"] += m["protein"]
        total["carbs"] += m["carbs"]
        total["fat"] += m["fat"]

    lines.append(
        f"\nTotal: ~{total['calories']} kcal\n"
        f"P: {total['protein']}g | C: {total['carbs']}g | F: {total['fat']}g"
    )

    return "\n".join(lines)


# ---- CLEAN JSON ----
def clean_json_output(text):
    text = text.strip()

    if text.startswith("```"):
        text = text.strip("`").replace("json", "").strip()

    if text.startswith("json"):
        text = text.replace("json", "", 1).strip()

    return text


# ---- AI: IMAGE → NUTRITION ----
def estimate_calories(image_url):
    ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

    res = requests.get(image_url, auth=(ACCOUNT_SID, AUTH_TOKEN))

    if res.status_code != 200:
        raise Exception(f"Image download failed: {res.status_code}")

    image_base64 = base64.b64encode(res.content).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{image_base64}"

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": """
Identify the food and estimate nutrition.

Return ONLY JSON:
{
"name": "...",
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
    cleaned = clean_json_output(raw)

    return json.loads(cleaned)


# ---- WEBHOOK ----
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.form()
    data = dict(data)

    print("INCOMING:", data)

    num_media = int(data.get("NumMedia", 0))
    body = data.get("Body", "").lower()

    if num_media > 0:
        image_url = data.get("MediaUrl0")

        try:
            est = estimate_calories(image_url)

            entry = {
                "name": est["name"],
                "calories": est["calories"],
                "protein": est["protein"],
                "carbs": est["carbs"],
                "fat": est["fat"],
                "image_url": image_url,
                "timestamp": datetime.now()
            }

            save_meal(entry)

            totals = get_today_totals()

            reply = (
                f"{est['name']} (~{est['calories']} kcal)\n"
                f"P: {est['protein']}g | "
                f"C: {est['carbs']}g | "
                f"F: {est['fat']}g\n\n"
                f"Today: {totals['calories']} kcal\n"
                f"P: {totals['protein']}g | "
                f"C: {totals['carbs']}g | "
                f"F: {totals['fat']}g"
            )

        except Exception as e:
            print("AI ERROR:", e)
            reply = f"ERROR: {str(e)}"

    elif body:
        try:
            if is_question(body):
                reply = answer_query(body)
            else:
                reply = "Logged"
        except Exception as e:
            reply = f"ERROR: {str(e)}"

    else:
        reply = "Send meal photo or ask a question"

    return Response(
        content=f"<Response><Message>{reply}</Message></Response>",
        media_type="application/xml"
    )
