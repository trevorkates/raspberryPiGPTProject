#!/usr/bin/env python3
import os
import time
import base64
from dotenv import load_dotenv
from openai import OpenAI

# ─── CONFIG ────────────────────────────────────────────────────────────────
load_dotenv()  # looks for OPENAI_API_KEY in the same folder or your env
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

WATCH_DIR      = "/home/keyence/iv3_images"
CHECK_INTERVAL = 5  # seconds between folder scans

# Optional few‑shot examples
REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT – Clean IML sticker, clear and centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT – White streaks are clearly visible in the print layer.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT – Shine is due to lighting reflection, not a defect."
}
# ────────────────────────────────────────────────────────────────────────────

def list_images():
    return sorted(
        f for f in os.listdir(WATCH_DIR)
        if f.lower().endswith((".jpg", ".jpeg"))
    )

def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def classify_image(path):
    # Build the chat prompt
    system = (
        "You are an expert lid inspector. Return exactly:\n"
        "ACCEPT or REJECT – reason. Confidence 0–100%."
    )
    messages = [{"role":"system","content":system}]
    for url, expl in REFERENCE_EXAMPLES.items():
        messages.append({"role":"user","content":f"{expl} Image: {url}"})
    b64 = encode_image(path)
    messages.append({
        "role":"user",
        "content":[
            {"type":"text","text":"Now evaluate this image:"},
            {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}} 
        ]
    })

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=messages
    )
    return resp.choices[0].message.content.strip()

def main():
    processed = set()
    while True:
        for img in list_images():
            if img in processed:
                continue
            full_path = os.path.join(WATCH_DIR, img)
            print(f"\n▶️ Processing: {img}")
            try:
                result = classify_image(full_path)
                print(f"✅ Result: {result}")
            except Exception as e:
                print(f"❌ Error: {e}")
            processed.add(img)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    print(f"👀 Watching {WATCH_DIR} every {CHECK_INTERVAL}s …")
    main()
