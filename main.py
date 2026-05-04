# (only webhook + small helpers changed, rest unchanged)

def get_today_meals_text(user_id):
    conn = get_conn()
    cur = conn.cursor()

    today = datetime.now().date()

    cur.execute("""
        SELECT name, grams, calories
        FROM meals
        WHERE user_id=%s AND DATE(timestamp)=%s
    """, (user_id, today))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    if not rows:
        return "No meals logged today."

    lines = []
    total = 0

    for r in rows:
        lines.append(f"- {r[0]} (~{r[1]}g, {r[2]} kcal)")
        total += r[2]

    lines.append(f"\nTotal: {total} kcal")

    return "\n".join(lines)


def set_user_name(user_id, new_name):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "UPDATE users SET name=%s WHERE id=%s",
        (new_name, user_id)
    )

    conn.commit()
    cur.close()
    conn.close()


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.form()
    data = dict(data)

    phone = data.get("From")
    profile_name = data.get("ProfileName", "Unknown")

    user_id = get_or_create_user(phone, profile_name)

    num_media = int(data.get("NumMedia", 0))
    body = data.get("Body", "").lower()

    # ================= IMAGE =================
    if num_media > 0:
        image_url = data.get("MediaUrl0")

        try:
            est = estimate_calories(image_url)

            save_meal(user_id, {
                "name": est["name"],
                "grams": est["grams"],
                "calories": est["calories"],
                "protein": est["protein"],
                "carbs": est["carbs"],
                "fat": est["fat"],
                "image_url": image_url,
                "timestamp": datetime.now()
            })

            save_log(user_id, "meal")

            reply = (
                f"{est['name']} (~{est['grams']}g, {est['calories']} kcal)\n"
                f"P: {est['protein']}g | "
                f"C: {est['carbs']}g | "
                f"F: {est['fat']}g"
            )

        except Exception as e:
            reply = f"ERROR: {str(e)}"

    # ================= TEXT =================
    elif body:

        # ---- COMMAND: set name ----
        if body.startswith("name "):
            new_name = body.replace("name ", "").strip()
            set_user_name(user_id, new_name)
            reply = f"Name set to {new_name}"

        # ---- COMMAND: show my meals ----
        elif body == "me":
            reply = get_today_meals_text(user_id)

        # ---- COMMAND: summary ----
        elif body == "summary":
            reply = build_daily_summary()

        else:
            save_log(user_id, "text")
            reply = "Logged"

    else:
        reply = "Send photo or log"

    return Response(
        content=f"<Response><Message>{reply}</Message></Response>",
        media_type="application/xml"
    )
