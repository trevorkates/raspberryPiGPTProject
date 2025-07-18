#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import base64
import threading
import tkinter as tk
from PIL import Image, ImageTk, ImageEnhance
from gpiozero import OutputDevice
import openai
from dotenv import load_dotenv
from io import BytesIO

# --- CONFIG --------------------------------------------------------
load_dotenv("/home/keyence/inspector/.env")
openai.api_key = os.getenv("OPENAI_API_KEY")

FOLDER_PATH   = "/home/keyence/iv3_images"
POLL_INTERVAL = 2  # check every 2 seconds

# GPIO pin for ACCEPT signal
accept_output = OutputDevice(19, active_high=True, initial_value=False)

# Few-shot examples to guide GPT-4 Vision
REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT - Clean IML sticker, clear and centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT - White streaks are clearly visible in the print layer.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT - Shine is due to lighting reflection, not a defect."
}

# --- HELPERS -------------------------------------------------------
def list_images():
    return sorted(
        f for f in os.listdir(FOLDER_PATH)
        if f.lower().endswith((".jpg", ".jpeg"))
    )

def is_file_stable(path, wait_time=1.0):
    size1 = os.path.getsize(path)
    time.sleep(wait_time)
    size2 = os.path.getsize(path)
    return size1 == size2

def clean_jpeg(path):
    try:
        with Image.open(path) as img:
            # Enhance brightness slightly for dark lids
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(1.2)  # boost brightness by 20%

            with BytesIO() as buffer:
                img.save(buffer, format="JPEG", quality=90)
                return base64.b64encode(buffer.getvalue()).decode()
    except Exception as e:
        print(f"JPEG cleanup error: {e}")
        return None

def classify_image(path, sensitivity, no_brand_mode):
    levels = {
        1: "Accept nearly everything, even with obvious imperfections.",
        2: "Accept mild streaks or small misprints. Reject only major flaws.",
        3: "Balanced - Reject unclear or misaligned branding or IML.",
        4: "Strict - Minor streaks or off-center prints may be REJECTED.",
        5: "Very strict - Any defect should result in REJECT."
    }

    focus = (
        "Focus solely on flash, surface quality, and color consistency. Ignore any branding or IML label."
        if no_brand_mode else
        levels[sensitivity] + " If no branding or IML sticker is visible, treat that as a defect and REJECT."
    )

    system_prompt = (
        "You are an expert lid inspector given a base64-encoded image. "
        "You have direct visual access via the encoded image. "
        "Never say you cannot evaluate. Even if blurry, unclear, or low-resolution, you must make your best judgment. "
        "Return exactly 'ACCEPT - reason (Confidence: XX%)' or 'REJECT - reason (Confidence: XX%)'. "
        f"Ensure confidence is a high value between 90% and 100%. Strictness {sensitivity}/5: {focus} "
        "Note: shine from lighting reflection is not a defect; do not confuse reflections with streaks."
    )

    messages = [{"role": "system", "content": system_prompt}]
    if not no_brand_mode:
        for url, example in REFERENCE_EXAMPLES.items():
            messages.append({"role": "user", "content": f"{example} Image: {url}"})

    b64 = clean_jpeg(path)
    if not b64:
        return "Error: Unable to clean and encode JPEG."

    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": "Now evaluate this image:"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}
        ]
    })
    resp = openai.ChatCompletion.create(model="gpt-4o", messages=messages)
    return resp.choices[0].message.content.strip()

# --- UI APP --------------------------------------------------------
class LidInspectorApp:
    def __init__(self, root):
        root.title("CM1 Lid Inspector")
        root.geometry("800x600")
        root.configure(bg="white")

        container = tk.Frame(root, bg="white")
        container.pack(fill="both", expand=True, padx=10, pady=10)

        self.left = tk.Frame(container, bg="white", width=400, height=600)
        self.right = tk.Frame(container, bg="white", width=380, height=600)
        self.left.pack(side="left", fill="both")
        self.right.pack(side="right", fill="y")
        self.left.pack_propagate(False)
        self.right.pack_propagate(False)

        self.image_label = tk.Label(self.left, bg="white")
        self.image_label.pack(fill="both", expand=True)

        logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            img.thumbnail((100, 100), Image.ANTIALIAS)
            self.logo_tk = ImageTk.PhotoImage(img)
            tk.Label(self.right, image=self.logo_tk, bg="white").pack(pady=(0, 10))

        self.start_btn = tk.Button(
            self.right,
            text="Start Inspection",
            command=self.start_inspection,
            bg="#4CAF50", fg="white", font=("Helvetica", 12, "bold"), height=2
        )

        self.slider_lbl = tk.Label(self.right, text="Strictness (1-5):", bg="white")
        self.sensitivity = tk.Scale(
            self.right, from_=1, to=5, orient="horizontal",
            bg="white", command=lambda _: self.display_image(force=True)
        )
        self.no_brand_var = tk.BooleanVar(value=False)
        self.no_brand_cb = tk.Checkbutton(
            self.right, text="No Brand/IML Mode", variable=self.no_brand_var,
            bg="lightgrey", width=20, command=lambda: self.display_image(force=True)
        )
        self.result_lbl = tk.Label(
            self.right, font=("Helvetica", 14), wraplength=260,
            justify="left", bg="white"
        )
        self.next_btn = tk.Button(self.right, text="Next Image", command=self.next_image)
        self.clear_srv_btn = tk.Button(self.right, text="Clear Server Photos", command=self.clear_server)

        for w in (
            self.start_btn, self.slider_lbl, self.sensitivity,
            self.no_brand_cb, self.result_lbl, self.next_btn, self.clear_srv_btn
        ):
            w.pack(pady=6, fill="x")

        self.sensitivity.set(3)
        self.images = []
        self.idx = 0
        self.seen = set()
        self.analyzing = False
        self.poll_thr = threading.Thread(target=self.watch_folder, daemon=True)

    def start_inspection(self):
        self.start_btn.pack_forget()
        self.images = list_images()
        self.seen = set(self.images)
        self.idx = 0
        if self.images:
            self.display_image()
        self.poll_thr.start()

    def watch_folder(self):
        while True:
            if not self.analyzing:
                current = set(list_images())
                new = sorted(current - self.seen)
                if new:
                    self.images.extend(new)
                    self.idx = len(self.images) - len(new)
                    self.display_image()
                    self.seen = current
            time.sleep(POLL_INTERVAL)

    def display_image(self, force=False):
        if self.idx >= len(self.images):
            self.result_lbl.config(text="All images reviewed.", fg="black")
            return

        path = os.path.join(FOLDER_PATH, self.images[self.idx])
        if not is_file_stable(path):
            self.result_lbl.config(fg="red", text="Skipping unstable file. Will retry.")
            return

        try:
            img = Image.open(path)
            img.thumbnail((400, 300), Image.ANTIALIAS)
            self.tkimg = ImageTk.PhotoImage(img)
            self.image_label.config(image=self.tkimg)
            self.result_lbl.config(text="", fg="black")
        except Exception as e:
            self.result_lbl.config(fg="red", text=f"Load error: {e}")
            return

        threading.Thread(target=self.analyze, args=(path,), daemon=True).start()

    def analyze(self, path):
        self.analyzing = True
        self.result_lbl.config(fg="orange", text="Analyzing...")
        accept_output.off()

        try:
            verdict = classify_image(path, self.sensitivity.get(), self.no_brand_var.get())
            color = "green" if verdict.upper().startswith("ACCEPT") else "red"
            self.result_lbl.config(fg=color, text=verdict)

            if verdict.upper().startswith("ACCEPT"):
                accept_output.on()
            else:
                accept_output.off()
        except Exception as e:
            self.result_lbl.config(fg="red", text=f"Error: {e}")
            accept_output.off()
        finally:
            self.analyzing = False

    def next_image(self):
        self.idx += 1
        self.display_image()

    def clear_server(self):
        for fname in os.listdir(FOLDER_PATH):
            try:
                os.remove(os.path.join(FOLDER_PATH, fname))
            except Exception as e:
                print(f"Error deleting {fname}: {e}")
        self.images = []
        self.seen = set()
        self.idx = 0
        self.image_label.config(image="")
        self.result_lbl.config(text="Server cleared - folder is now empty.", fg="black")

if __name__ == "__main__":
    root = tk.Tk()
    app = LidInspectorApp(root)
    root.mainloop()
