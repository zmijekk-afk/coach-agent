from fastapi import FastAPI, Request
from fastapi.responses import Response
import json
from datetime import datetime
import os
from openai import OpenAI

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
    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Estimate calories of this meal. Be concise. Format: '~500 kcal (medium confidence)'."
                },
                {
                    "type": "input_image",
                    "image_url": image_url
                }
            ]
        }]
    )

    return response.output_text.strip()


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
