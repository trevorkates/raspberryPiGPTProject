#!/usr/bin/env python3
import os
import time
import base64
import threading
from PIL import Image, ImageTk
import tkinter as tk
import openai
from dotenv import load_dotenv

# ─── CONFIG ────────────────────────────────────────────────────────────────
load_dotenv("/home/keyence/inspector/.env")
openai.api_key = os.getenv("OPENAI_API_KEY")

FOLDER_PATH   = "/home/keyence/iv3_images"
POLL_INTERVAL = 5  # seconds between checks

REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT – Clean IML sticker, clear and centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT – White streaks are clearly visible in the print layer.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT – Shine is due to lighting reflection, not a defect."
}

# ─── HELPER FUNCTIONS ────────────────────────────────────────────────────────
def list_images():
    return sorted(f for f in os.listdir(FOLDER_PATH) if f.lower().endswith((".jpg", ".jpeg")))

def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def classify_image(image_path, sensitivity):
    levels = {
        1: "Accept nearly everything, even with obvious imperfections.",
        2: "Accept mild streaks or small misprints. Reject only major flaws.",
        3: "Balanced – Reject unclear or misaligned branding or IML.",
        4: "Strict – Minor streaks or off-center prints may be REJECTED.",
        5: "Very strict – Any defect should result in REJECT."
    }
    system_prompt = (
        "You are an expert lid inspector. Classify whether a trash-can lid image "
        "should be ACCEPTED or REJECTED. Return exactly 'ACCEPT – reason' or 'REJECT – reason'. "
        f"Strictness {sensitivity}/5: {levels[sensitivity]}"
    )
    msgs = [{"role":"system","content":system_prompt}]
    for url, expl in REFERENCE_EXAMPLES.items():
        msgs.append({"role":"user","content":f"{expl} Image: {url}"})
    b64 = encode_image(image_path)
    msgs.append({
        "role":"user",
        "content":[
            {"type":"text","text":"Now evaluate this image:"},
            {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}} 
        ]
    })
    resp = openai.ChatCompletion.create(model="gpt-4o", messages=msgs)
    return resp.choices[0].message.content.strip()

# ─── APPLICATION ────────────────────────────────────────────────────────────
class LidInspectorApp:
    def __init__(self, root):
        root.title("Trash Lid Inspector")
        root.geometry("800x600")
        root.configure(bg="white")

        # Container frames
        container = tk.Frame(root, bg="white")
        container.pack(fill="both", expand=True, padx=10, pady=10)

        self.left_frame  = tk.Frame(container, bg="white")
        self.right_frame = tk.Frame(container, bg="white")
        self.left_frame.pack(side="left", fill="both", expand=True)
        self.right_frame.pack(side="right", fill="y")

        # Image display
        self.image_label = tk.Label(self.left_frame, bg="white")
        self.image_label.pack(fill="both", expand=True)

        # Controls on the right
        self.start_btn    = tk.Button(self.right_frame, text="Start Inspection", command=self.start_inspection)
        self.slider_lbl   = tk.Label(self.right_frame, text="Strictness (1–5):", bg="white")
        self.sensitivity  = tk.Scale(self.right_frame, from_=1, to=5, orient="horizontal", bg="white",
                                     command=lambda _: self.display_image(force=True))
        self.result_lbl   = tk.Label(self.right_frame, font=("Helvetica",14), bg="white")
        self.next_btn     = tk.Button(self.right_frame, text="Next Image", command=self.next_image)

        # Pack controls with spacing
        for w in (self.start_btn, self.slider_lbl, self.sensitivity, self.result_lbl, self.next_btn):
            w.pack(pady=8, fill="x")

        self.sensitivity.set(3)

        # Internal state
        self.image_list    = []
        self.index         = 0
        self.poll_thread   = threading.Thread(target=self.watch_folder, daemon=True)

    def start_inspection(self):
        self.start_btn.pack_forget()
        self.image_list = list_images()
        if self.image_list:
            self.display_image()
        self.poll_thread.start()

    def watch_folder(self):
        seen = set(self.image_list)
        while True:
            current = set(list_images())
            new = sorted(current - seen)
            if new:
                self.image_list.extend(new)
                self.index = len(self.image_list) - len(new)
                self.display_image()
                seen = current
            time.sleep(POLL_INTERVAL)

    def display_image(self, force=False):
        if self.index >= len(self.image_list):
            self.result_lbl.config(text="All images reviewed.")
            return

        path = os.path.join(FOLDER_PATH, self.image_list[self.index])
        try:
            img = Image.open(path)
            img.thumbnail((400,300), Image.ANTIALIAS)
            self.tkimg = ImageTk.PhotoImage(img)
            self.image_label.config(image=self.tkimg)
            self.result_lbl.config(text="")
        except Exception as e:
            self.result_lbl.config(fg="red", text=f"Load error: {e}")
            return

        threading.Thread(target=self.analyze, args=(path,), daemon=True).start()

    def analyze(self, path):
        self.result_lbl.config(fg="orange", text="Analyzing…")
        try:
            verdict = classify_image(path, self.sensitivity.get())
            color   = "green" if verdict.upper().startswith("ACCEPT") else "red"
            self.result_lbl.config(fg=color, text=verdict)
        except Exception as e:
            self.result_lbl.config(fg="red", text=f"Error: {e}")

    def next_image(self):
        self.index += 1
        self.display_image()

if __name__=="__main__":
    root = tk.Tk()
    app  = LidInspectorApp(root)
    root.mainloop()
