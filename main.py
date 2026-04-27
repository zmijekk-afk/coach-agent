from fastapi import FastAPI, Request
from fastapi.responses import Response
import json
from datetime import datetime

app = FastAPI()


@app.get("/")
def home():
    return {"status": "running"}


# ---- Helper function to save logs ----
def save_log(entry):
    try:
        with open("logs.json", "r") as f:
            data = json.load(f)
    except:
        data = []

    data.append(entry)

    with open("logs.json", "w") as f:
        json.dump(data, f)


# ---- Webhook ----
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.form()
        data = dict(data)
    except:
        data = await request.json()

    print("INCOMING:", data)

    body = data.get("Body", "").lower()

    if body:
        entry = {
            "text": body,
            "timestamp": datetime.utcnow().isoformat()
        }

        save_log(entry)

        reply = f"Logged: {body}"
    else:
        reply = "Send something to log"

    return Response(
        content=f"""
        <Response>
            <Message>{reply}</Message>
        </Response>
        """,
        media_type="application/xml"
    )
