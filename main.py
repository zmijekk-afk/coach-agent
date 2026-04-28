from fastapi import FastAPI, Request
from fastapi.responses import Response
import json
from datetime import datetime
import os
from openai import OpenAI
import requests
import base64
import psycopg2

app = FastAPI()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---- DATABASE CONNECTION ----
DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL, sslmode='require')
cursor = conn.cursor()

# ---- CREATE TABLE (runs once) ----
cursor.execute("""
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


@app.get("/")
def home():
    return {"status": "running"}


# ---- SAVE MEAL ----
def save_meal(entry):
    cursor.execute("""
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


# ---- LOAD TODAY MEALS ----
def load_today_meals():
    today = datetime.now().date()

    cursor.execute("""
        SELECT name, calories, protein, carbs, fat, timestamp
        FROM meals
        WHERE DATE(timestamp) = %s
    """, (today,))

    rows = cursor.fetchall()

    meals = []
    for r in rows:
        meals.append({
            "name": r[0],
            "calories": r[1],
            "protein": r[2],
            "carbs": r[3],
            "fat": r[4],
            "timestamp": r[5].isoformat()
        })

    return meals


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


# ---- QUESTION DETECTION ----
def is_question(text):
    response = client.responses.create(
        model="gpt-4o-mini",
        input=f"""
Message: "{text}"

Does this message request information about past meals, calories, or activity?

Respond ONLY with:
yes
or
no

Be liberal: if unsure, answer yes.
"""
    )

    return "yes" in response.output_text.lower()


# ---- QUERY ANSWER ----
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


# ---- AI ----
def estimate_calories(image_url):
    ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

    res = requests.get(image_url, auth=(ACCOUNT_SID, AUTH_TOKEN))
    img_base64 = base64.b64encode(res.content).decode("utf-8")

    data_url = f"data:image/jpeg;base64,{img_base64}"

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
    return json.loads(clean_json_output(raw))


# ---- WEBHOOK ----
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.form()
        data = dict(data)
    except:
        data = await request.json()

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
                f"P: {est['protein']}g | C: {est['carbs']}g | F: {est['fat']}g\n\n"
                f"Today: {totals['calories']} kcal\n"
                f"P: {totals['protein']}g | C: {totals['carbs']}g | F: {totals['fat']}g"
            )

        except Exception as e:
            reply = f"ERROR: {str(e)}"

    elif body:
        if is_question(body):
            reply = answer_query(body)
        else:
            reply = "Logged"

    else:
        reply = "Send photo or question"

    return Response(
        content=f"<Response><Message>{reply}</Message></Response>",
        media_type="application/xml"
    )
