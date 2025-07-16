#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, time, base64, threading
import tkinter as tk
from PIL import Image, ImageTk
import openai
from dotenv import load_dotenv

# --- CONFIG --------------------------------------------------------
load_dotenv("/home/keyence/inspector/.env")
openai.api_key = os.getenv("OPENAI_API_KEY")

FOLDER_PATH   = "/home/keyence/iv3_images"
POLL_INTERVAL = 5

REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT - Clean IML sticker, clear and centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT - White streaks are clearly visible in the print layer.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT - Shine is due to lighting reflection, not a defect."
}

def list_images():
    return sorted(f for f in os.listdir(FOLDER_PATH) if f.lower().endswith((".jpg", ".jpeg")))

def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def classify_image(path, sensitivity):
    levels = {
        1: "Accept nearly everything, even with obvious imperfections.",
        2: "Accept mild streaks or small misprints. Reject only major flaws.",
        3: "Balanced - Reject unclear or misaligned branding or IML.",
        4: "Strict - Minor streaks or off-center prints may be REJECTED.",
        5: "Very strict - Any defect should result in REJECT."
    }
    prompt = (
        "You are an expert lid inspector. Return exactly 'ACCEPT - reason' or 'REJECT - reason'. "
        f"Strictness {sensitivity}/5: {levels[sensitivity]}"
    )
    msgs = [{"role":"system","content":prompt}]
    for url, expl in REFERENCE_EXAMPLES.items():
        msgs.append({"role":"user","content":f"{expl} Image: {url}"})
    b64 = encode_image(path)
    msgs.append({
        "role":"user",
        "content":[
            {"type":"text","text":"Now evaluate this image:"},
            {"type":"image_url","image_url":{"url":"data:image/jpeg;base64," + b64}}
        ]
    })
    resp = openai.ChatCompletion.create(model="gpt-4o", messages=msgs)
    return resp.choices[0].message.content.strip()

# --- APPLICATION ----------------------------------------------------
class LidInspectorApp:
    def __init__(self, root):
        root.title("Trash Lid Inspector")
        root.geometry("800x600")
        root.configure(bg="white")

        # main container
        container = tk.Frame(root, bg="white")
        container.pack(fill="both", expand=True, padx=10, pady=10)

        # left: image; right: controls
        self.left  = tk.Frame(container, bg="white")
        self.right = tk.Frame(container, bg="white")
        self.left.pack(side="left", fill="both", expand=True)
        self.right.pack(side="right", fill="y")

        # LEFT: image display
        self.image_label = tk.Label(self.left, bg="white")
        self.image_label.pack(fill="both", expand=True)

        # RIGHT TOP: logo
        logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
        if os.path.exists(logo_path):
            logo_img = Image.open(logo_path)
            logo_img.thumbnail((100,100), Image.ANTIALIAS)
            self.logo_tk = ImageTk.PhotoImage(logo_img)
            tk.Label(self.right, image=self.logo_tk, bg="white").pack(pady=(0,10))

        # RIGHT: controls
        self.start_btn   = tk.Button(self.right, text="Start Inspection", command=self.start_inspection)
        self.slider_lbl  = tk.Label(self.right, text="Strictness (1-5):", bg="white")
        self.sensitivity = tk.Scale(self.right, from_=1, to=5, orient="horizontal",
                                    bg="white", command=lambda _: self.display_image(force=True))
        # wraplength ensures full text is visible
        self.result_lbl  = tk.Label(self.right, font=("Helvetica",14),
                                    wraplength=250, justify="left", bg="white")
        self.next_btn    = tk.Button(self.right, text="Next Image", command=self.next_image)

        for w in (self.start_btn, self.slider_lbl, self.sensitivity, self.result_lbl, self.next_btn):
            w.pack(pady=6, fill="x")

        self.sensitivity.set(3)

        # internal state
        self.images   = []
        self.idx      = 0
        self.poll_thr = threading.Thread(target=self.watch_folder, daemon=True)

    def start_inspection(self):
        self.start_btn.pack_forget()
        self.images = list_images()
        if self.images:
            self.display_image()
        self.poll_thr.start()

    def watch_folder(self):
        seen = set(self.images)
        while True:
            current = set(list_images())
            new = sorted(current - seen)
            if new:
                self.images.extend(new)
                self.idx = len(self.images) - len(new)
                self.display_image()
                seen = current
            time.sleep(POLL_INTERVAL)

    def display_image(self, force=False):
        if self.idx >= len(self.images):
            self.result_lbl.config(text="All images reviewed.")
            return

        path = os.path.join(FOLDER_PATH, self.images[self.idx])
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
        self.result_lbl.config(fg="orange", text="Analyzing...")
        try:
            verdict = classify_image(path, self.sensitivity.get())
            color   = "green" if verdict.upper().startswith("ACCEPT") else "red"
            self.result_lbl.config(fg=color, text=verdict)
        except Exception as e:
            self.result_lbl.config(fg="red", text=f"Error: {e}")

    def next_image(self):
        self.idx += 1
        self.display_image()

if __name__=="__main__":
    root = tk.Tk()
    app  = LidInspectorApp(root)
    root.mainloop()
