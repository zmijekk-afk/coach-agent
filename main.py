from fastapi import FastAPI, Request
from fastapi.responses import Response
import json
from datetime import datetime
import os
from openai import OpenAI
import requests
import base64
import threading
import time

app = FastAPI()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 👉 YOUR WHATSAPP NUMBER (replace this)
USER_NUMBER = "whatsapp:+48533913613"

TWILIO_NUMBER = "whatsapp:+14155238886"

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")


@app.get("/")
def home():
    return {"status": "running"}


# ---- SEND WHATSAPP MESSAGE ----
def send_whatsapp_message(body):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json"

    data = {
        "From": TWILIO_NUMBER,
        "To": USER_NUMBER,
        "Body": body
    }

    requests.post(url, data=data, auth=(ACCOUNT_SID, AUTH_TOKEN))


# ---- REMINDER LOOP ----
def reminder_loop():
    last_sent = None

    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        # Send at 08:40 once per day
        if current_time == "08:40":
            today = now.date()

            if last_sent != today:
                print("Sending morning reminder...")

                send_whatsapp_message(
                    "Siema byczq, pamiętaj o foteczkach 📸"
                )

                last_sent = today

        time.sleep(60)


# ---- START BACKGROUND THREAD ----
threading.Thread(target=reminder_loop, daemon=True).start()


# ---- Save logs ----
def save_log(entry):
    try:
        with open("logs.json", "r") as f:
            data = json.load(f)
    except:
        data = []

    data.append(entry)

    with open("logs.json", "w") as f:
        json.dump(data, f)


def load_logs():
    try:
        with open("logs.json", "r") as f:
            return json.load(f)
    except:
        return []


def get_today_totals():
    logs = load_logs()
    today = datetime.now().date()

    totals = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    for entry in logs:
        if entry.get("type") != "meal":
            continue

        try:
            ts = datetime.fromisoformat(entry["timestamp"]).date()
        except:
            continue

        if ts == today:
            totals["calories"] += entry.get("calories", 0)
            totals["protein"] += entry.get("protein", 0)
            totals["carbs"] += entry.get("carbs", 0)
            totals["fat"] += entry.get("fat", 0)

    return totals


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


def answer_query(user_text):
    logs = load_logs()
    today = datetime.now().date()

    meals = []

    for entry in logs:
        if entry.get("type") != "meal":
            continue

        try:
            ts = datetime.fromisoformat(entry["timestamp"]).date()
        except:
            continue

        if ts == today:
            meals.append(entry)

    if not meals:
        return "No meals logged today."

    lines = []
    total = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    for m in meals:
        name = m.get("name", "Meal")
        kcal = m.get("calories", 0)
        protein = m.get("protein", 0)
        carbs = m.get("carbs", 0)
        fat = m.get("fat", 0)

        total["calories"] += kcal
        total["protein"] += protein
        total["carbs"] += carbs
        total["fat"] += fat

        lines.append(f"- {name} (~{kcal} kcal)")

    lines.append(
        f"\nTotal: ~{total['calories']} kcal\n"
        f"P: {total['protein']}g | "
        f"C: {total['carbs']}g | "
        f"F: {total['fat']}g"
    )

    return "\n".join(lines)


def clean_json_output(output_text):
    text = output_text.strip()

    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "").strip()

    if text.startswith("json"):
        text = text.replace("json", "", 1).strip()

    return text


def estimate_calories(image_url):
    response = requests.get(image_url, auth=(ACCOUNT_SID, AUTH_TOKEN))
    if response.status_code != 200:
        raise Exception(f"Download failed: {response.status_code}")

    image_base64 = base64.b64encode(response.content).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{image_base64}"

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": """
Identify the food and estimate nutrition.

Return ONLY valid JSON:
{
  "name": "short food name",
  "calories": number,
  "protein": number,
  "carbs": number,
  "fat": number,
  "confidence": "low|medium|high"
}
"""
                    },
                    {
                        "type": "input_image",
                        "image_url": data_url
                    }
                ]
            }
        ]
    )

    raw = response.output[0].content[0].text
    cleaned = clean_json_output(raw)

    return json.loads(cleaned)


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
            estimate = estimate_calories(image_url)

            entry = {
                "type": "meal",
                "name": estimate.get("name", "Meal"),
                "image_url": image_url,
                "calories": estimate["calories"],
                "protein": estimate["protein"],
                "carbs": estimate["carbs"],
                "fat": estimate["fat"],
                "timestamp": datetime.now().isoformat()
            }

            save_log(entry)

            totals = get_today_totals()

            reply = (
                f"{estimate['name']} (~{estimate['calories']} kcal)\n"
                f"P: {estimate['protein']}g | "
                f"C: {estimate['carbs']}g | "
                f"F: {estimate['fat']}g\n\n"
                f"Today total: {totals['calories']} kcal\n"
                f"P: {totals['protein']}g | "
                f"C: {totals['carbs']}g | "
                f"F: {totals['fat']}g"
            )

        except Exception as e:
            reply = f"ERROR: {str(e)}"

    elif body:
        if is_question(body):
            reply = answer_query(body)
        else:
            save_log({
                "type": "text",
                "text": body,
                "timestamp": datetime.now().isoformat()
            })
            reply = f"Logged: {body}"

    else:
        reply = "Send a meal photo or ask a question"

    return Response(
        content=f"<Response><Message>{reply}</Message></Response>",
        media_type="application/xml"
    )
