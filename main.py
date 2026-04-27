from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/")
def home():
    return {"status": "running"}

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.form()
        data = dict(data)
    except:
        data = await request.json()

    print("INCOMING:", data)

   from fastapi.responses import Response

return Response(
    content="""
    <Response>
        <Message>Got it 👍</Message>
    </Response>
    """,
    media_type="application/xml"
)
