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


# ---- Health check ----
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


# ---- Get today's totals (still useful for quick replies) ----
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


# ---- Detect if message is a question (multilingual) ----
def is_question(text):
    response = client.responses.create(
        model="gpt-4o-mini",
        input=f"""
Classify this message:

"{text}"

Is the user asking for information, summary, or insight about their past meals, calories, nutrition, or activity?

Examples of YES:
- how many calories today
- what did I eat today
- ile kalorii dzisiaj
- co dzisiaj jadłem

Examples of NO:
- chicken and rice
- gym workout done
- hello

Answer ONLY:
yes
or
no
"""
    )

    return "yes" in response.output_text.lower()


# ---- Answer query using logs ----
def answer_query(user_text):
    logs = load_logs()

    # limit context size
    recent_logs = logs[-30:]

    response = client.responses.create(
        model="gpt-4o-mini",
        input=f"""
User question:
{user_text}

Here is their recent tracked data:
{json.dumps(recent_logs)}

Instructions:
- If asking about today → summarize today's meals and calories
- If asking about food → list meals
- If asking about calories → compute totals
- Keep answer short and clear
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

    # Download image from Twilio
    response = requests.get(image_url, auth=(account_sid, auth_token))
    if response.status_code != 200:
        raise Exception(f"Download failed: {response.status_code}")

    # Convert image to base64
    image_bytes = response.content
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{image_base64}"

    # Send to OpenAI
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
