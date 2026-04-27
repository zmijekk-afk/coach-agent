from fastapi import FastAPI, Request
from fastapi.responses import Response

app = FastAPI()

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
