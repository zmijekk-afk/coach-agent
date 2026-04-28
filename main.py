from fastapi import FastAPI, Request
from fastapi.responses import Response
import json
from datetime import datetime
import os
from openai import OpenAI
import requests
import base64

app = FastAPI()

# ---- Initialize OpenAI client ----
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ---- Health check endpoint ----
@app.get("/")
def home():
    return {"status": "running"}


# ---- Save any event (meal, text, etc.) to logs.json ----
def save_log(entry):
    try:
        with open("logs.json", "r") as f:
            data = json.load(f)
    except:
        data = []

    data.append(entry)

    with open("logs.json", "w") as f:
        json.dump(data, f)


# ---- Calculate today's totals (calories + macros) ----
def get_today_totals():
    try:
        with open("logs.json", "r") as f:
            data = json.load(f)
    except:
        return {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    today = datetime.utcnow().date()

    totals = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    for entry in data:
        if entry.get("type") != "meal":
            continue

        ts = datetime.fromisoformat(entry["timestamp"]).date()

        if ts == today:
            totals["calories"] += entry.get("calories", 0)
            totals["protein"] += entry.get("protein", 0)
            totals["carbs"] += entry.get("carbs", 0)
            totals["fat"] += entry.get("fat", 0)

    return totals


# ---- AI: Estimate calories + macros from image ----
def estimate_calories(image_url):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    # Download image from Twilio (requires authentication)
    response = requests.get(image_url, auth=(account_sid, auth_token))

    if response.status_code != 200:
        raise Exception(f"Download failed: {response.status_code}")

    # Convert image to base64 so OpenAI can read it
    image_bytes = response.content
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    data_url = f"data:image/jpeg;base64,{image_base64}"

    # Send image to OpenAI
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

    output_text = response.output[0].content[0].text

    # Parse AI response into Python dict
    try:
        data = json.loads(output_text)
    except:
        raise Exception(f"Bad AI output: {output_text}")

    return data


# ---- Main webhook (handles WhatsApp messages) ----
@app.post("/webhook")
async def webhook(request: Request):
    # Parse incoming Twilio data
    try:
        data = await request.form()
        data = dict(data)
    except:
        data = await request.json()

    print("INCOMING:", data)

    num_media = int(data.get("NumMedia", 0))
    body = data.get("Body", "").lower()

    # ---- IMAGE CASE (meal logging) ----
    if num_media > 0:
        image_url = data.get("MediaUrl0")

        try:
            # 1. Get AI estimate
            estimate = estimate_calories(image_url)

            # 2. Save structured meal entry
            entry = {
                "type": "meal",
                "image_url": image_url,
                "calories": estimate["calories"],
                "protein": estimate["protein"],
                "carbs": estimate["carbs"],
                "fat": estimate["fat"],
                "timestamp": datetime.utcnow().isoformat()
            }

            save_log(entry)

            # 3. Get updated daily totals
            totals = get_today_totals()

            # 4. Build response
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

    # ---- TEXT CASE (training logs, notes, etc.) ----
    elif body:
        entry = {
            "type": "text",
            "text": body,
            "timestamp": datetime.utcnow().isoformat()
        }

        save_log(entry)

        reply = f"Logged: {body}"

    # ---- EMPTY CASE ----
    else:
        reply = "Send a meal photo or training log"

    # ---- Return WhatsApp response ----
    return Response(
        content=f"""
        <Response>
            <Message>{reply}</Message>
        </Response>
        """,
        media_type="application/xml"
    )
