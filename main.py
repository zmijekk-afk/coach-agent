from fastapi import FastAPI, Request
from fastapi.responses import Response
import json
from datetime import datetime
import os
from openai import OpenAI
import requests
import base64
app = FastAPI()

# ---- OpenAI client ----
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


@app.get("/")
def home():
    return {"status": "running"}


# ---- Helper: save logs ----
def save_log(entry):
    try:
        with open("logs.json", "r") as f:
            data = json.load(f)
    except:
        data = []

    data.append(entry)

    with open("logs.json", "w") as f:
        json.dump(data, f)


# ---- AI: estimate calories ----
def estimate_calories(image_url):
    import requests
    import base64

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    # Download image from Twilio (authenticated)
    response = requests.get(image_url, auth=(account_sid, auth_token))

    if response.status_code != 200:
        raise Exception(f"Download failed: {response.status_code}")

    image_bytes = response.content
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    # Convert to data URL (THIS is the key fix)
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
                        "text": "Estimate calories of this meal. Format: '~500 kcal (medium confidence)'."
                    },
                    {
                        "type": "input_image",
                        "image_url": data_url
                    }
                ]
            }
        ]
    )

    return response.output[0].content[0].text

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

        entry = {
            "type": "image",
            "image_url": image_url,
            "timestamp": datetime.utcnow().isoformat()
        }

        save_log(entry)

        try:
            estimate = estimate_calories(image_url)
            reply = estimate
        except Exception as e:
            print("AI ERROR:", e)
            reply = "Couldn't estimate calories. Try again."

    # ---- TEXT CASE ----
    elif body:
        entry = {
            "type": "text",
            "text": body,
            "timestamp": datetime.utcnow().isoformat()
        }

        save_log(entry)

        reply = f"Logged: {body}"

    # ---- EMPTY ----
    else:
        reply = "Send a meal photo or training log"

    return Response(
        content=f"""
        <Response>
            <Message>{reply}</Message>
        </Response>
        """,
        media_type="application/xml"
    )
