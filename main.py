from fastapi import FastAPI, Request
from fastapi.responses import Response
import json
from datetime import datetime
import os
from openai import OpenAI
import requests
import base64

app = FastAPI()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


@app.get("/")
def home():
    return {"status": "running"}


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


# ---- Load logs ----
def load_logs():
    try:
        with open("logs.json", "r") as f:
            return json.load(f)
    except:
        return []


# ---- Get today's totals ----
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


# ---- Detect if message is a question ----
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


# ---- FIXED QUERY HANDLER (lists meals properly) ----
def answer_query(user_text):
    logs = load_logs()

    today = datetime.now().date()
    today_meals = []

    # Filter today's meals
    for entry in logs:
        if entry.get("type") != "meal":
            continue

        try:
            ts = datetime.fromisoformat(entry["timestamp"]).date()
        except:
            continue

        if ts == today:
            today_meals.append(entry)

    if not today_meals:
        return "No meals logged today."

    # Ask AI to summarize properly
    response = client.responses.create(
        model="gpt-4o-mini",
        input=f"""
User question:
{user_text}

Meals today:
{json.dumps(today_meals)}

IMPORTANT:
- ALWAYS list each meal
- Use bullet points
- Include estimated calories per meal
- Then include TOTAL calories

Example:
- Apple (~95 kcal)
- Chicken and rice (~500 kcal)

Total: ~595 kcal

Be concise.
"""
    )

    return response.output_text.strip()


# ---- Clean AI JSON output ----
def clean_json_output(output_text):
    text = output_text.strip()

    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "").strip()

    if text.startswith("json"):
        text = text.replace("json", "", 1).strip()

    return text


# ---- AI: Estimate calories ----
def estimate_calories(image_url):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    response = requests.get(image_url, auth=(account_sid, auth_token))
    if response.status_code != 200:
        raise Exception(f"Download failed: {response.status_code}")

    image_bytes = response.content
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
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
Estimate the nutritional content of this meal.

Return ONLY valid JSON:
{
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

    raw_output = response.output[0].content[0].text
    cleaned = clean_json_output(raw_output)

    try:
        data = json.loads(cleaned)
    except:
        raise Exception(f"Bad AI output: {raw_output}")

    return data


# ---- Webhook ----
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.form()
        data = dict(data)
    except:
        data = await request.json()

    print("INCOMING:", data)

    num_media = int(data.get("NumMedia", 0))
    body = data.get("Body", "").lower()

    # ---- IMAGE CASE ----
    if num_media > 0:
        image_url = data.get("MediaUrl0")

        try:
            estimate = estimate_calories(image_url)

            entry = {
                "type": "meal",
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
                f"{estimate['calories']} kcal\n"
                f"P: {estimate['protein']}g | "
                f"C: {estimate['carbs']}g | "
                f"F: {estimate['fat']}g\n\n"
                f"Today total: {totals['calories']} kcal"
            )

        except Exception as e:
            print("AI ERROR:", str(e))
            reply = f"ERROR: {str(e)}"

    # ---- TEXT CASE ----
    elif body:
        try:
            if is_question(body):
                reply = answer_query(body)
            else:
                entry = {
                    "type": "text",
                    "text": body,
                    "timestamp": datetime.now().isoformat()
                }
                save_log(entry)
                reply = f"Logged: {body}"

        except Exception as e:
            reply = f"ERROR: {str(e)}"

    else:
        reply = "Send a meal photo or ask a question"

    return Response(
        content=f"""
        <Response>
            <Message>{reply}</Message>
        </Response>
        """,
        media_type="application/xml"
    )
