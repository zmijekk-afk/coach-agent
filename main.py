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


def save_log(entry):
    try:
        with open("logs.json", "r") as f:
            data = json.load(f)
    except:
        data = []

    data.append(entry)

    with open("logs.json", "w") as f:
        json.dump(data, f)


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

    output_text = response.output[0].content[0].text

    try:
        data = json.loads(output_text)
    except:
        raise Exception(f"Bad AI output: {output_text}")

    return data


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

            reply = (
                f"{estimate['calories']} kcal\n"
                f"P: {estimate['protein']}g | "
                f"C: {estimate['carbs']}g | "
                f"F: {estimate['fat']}g"
            )

        except Exception as e:
            print("AI ERROR:", str(e))
            reply = f"ERROR: {str(e)}"

    elif body:
        entry = {
            "type": "text",
            "text": body,
            "timestamp": datetime.utcnow().isoformat()
        }

        save_log(entry)

        reply = f"Logged: {body}"

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
