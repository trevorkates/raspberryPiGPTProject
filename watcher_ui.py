#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import base64
import threading
import tkinter as tk
from PIL import Image, ImageTk, ImageEnhance, ImageOps
from gpiozero import OutputDevice
import openai
from dotenv import load_dotenv
from io import BytesIO
import subprocess

# --- CONFIG --------------------------------------------------------
load_dotenv("/home/keyence/inspector/.env")
openai.api_key = os.getenv("OPENAI_API_KEY")

FOLDER_PATH   = "/home/keyence/iv3_images"
POLL_INTERVAL = 2  # seconds

accept_output = OutputDevice(19, active_high=True, initial_value=False)
reject_output = OutputDevice(22, active_high=True, initial_value=False)

REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT - Clean IML sticker, clear and centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT - White streaks are clearly visible in the print layer.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT - Shine is due to lighting reflection, not a defect."
}

def list_images():
    return sorted(
        f for f in os.listdir(FOLDER_PATH)
        if f.lower().endswith(('.jpg', '.jpeg'))
    )

def is_file_stable(path, wait_time=1.0):
    size1 = os.path.getsize(path)
    time.sleep(wait_time)
    size2 = os.path.getsize(path)
    return size1 == size2

def clean_jpeg(path):
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(1.2)
            with BytesIO() as buffer:
                img.save(buffer, format="JPEG", quality=90)
                return base64.b64encode(buffer.getvalue()).decode()
    except Exception as e:
        print(f"JPEG cleanup error: {e}")
        return None

def classify_image(path, sensitivity, no_brand_mode, attempt=0):
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

    try:
        resp = openai.ChatCompletion.create(model="gpt-4o", messages=messages)
        return resp.choices[0].message.content.strip()
    except Exception as e:
        if attempt < 2:
            time.sleep(2)
            return classify_image(path, sensitivity, no_brand_mode, attempt+1)
        return f"Error: {e}"

# --- UI --------------------------------------------------------
class LidInspectorApp:
    def __init__(self, root):
        root.title("CM1 Lid Inspector")
        root.geometry("880x660")
        root.configure(bg="white")

        container = tk.Frame(root, bg="white")
        container.pack(fill="both", expand=True, padx=10, pady=10)

        self.left = tk.Frame(container, bg="white", width=420, height=600)
        self.right = tk.Frame(container, bg="white", width=440, height=600)
        self.left.pack(side="left", fill="both")
        self.right.pack(side="right", fill="y")
        self.left.pack_propagate(False)
        self.right.pack_propagate(False)

        self.image_label = tk.Label(self.left, bg="white")
        self.image_label.pack(fill="both", expand=True)

        self.result_lbl = tk.Label(self.right, font=("Helvetica", 14), wraplength=360,
                                   justify="left", bg="white")
        self.progress_lbl = tk.Label(self.right, text="", bg="white")

        self.slider_lbl = tk.Label(self.right, text="Strictness (1-5):", bg="white")
        self.sensitivity = tk.Scale(self.right, from_=1, to=5, orient="horizontal",
                                    bg="white", length=280, font=("Helvetica", 11))

        self.no_brand_var = tk.BooleanVar(value=False)
        self.no_brand_cb = tk.Checkbutton(self.right, text="No Brand/IML Mode",
                                          variable=self.no_brand_var, bg="lightgrey")

        self.start_btn = tk.Button(self.right, text="▶ Start", command=self.start_inspection, bg="green", fg="white")
        self.next_btn = tk.Button(self.right, text="→ Next", command=self.next_image)
        self.clear_btn = tk.Button(self.right, text="🗑️ Clear Folder", command=self.clear_server)
        self.git_btn = tk.Button(self.right, text="🔄 Check for Update", command=self.update_repo)
        self.manual_accept = tk.Button(self.right, text="✔ Manual Accept", command=lambda: accept_output.on())
        self.manual_reject = tk.Button(self.right, text="✖ Manual Reject", command=lambda: reject_output.on())

        for w in (
            self.slider_lbl, self.sensitivity, self.no_brand_cb, self.result_lbl,
            self.progress_lbl, self.start_btn, self.next_btn, self.clear_btn,
            self.manual_accept, self.manual_reject, self.git_btn
        ):
            w.pack(pady=5, fill="x")

        self.images = []
        self.idx = 0
        self.analyzing = False
        self.poll_thr = threading.Thread(target=self.watch_folder, daemon=True)
        self.sensitivity.set(3)

    def start_inspection(self):
        self.images = list_images()
        self.idx = 0
        if self.images:
            self.display_image()
        self.poll_thr.start()

    def watch_folder(self):
        seen = set()
        while True:
            if not self.analyzing:
                current = set(list_images())
                new = sorted(current - seen)
                if new:
                    self.images.extend(new)
                    self.idx = len(self.images) - len(new)
                    self.display_image()
                    seen = current
            time.sleep(POLL_INTERVAL)

    def display_image(self):
        if self.idx >= len(self.images):
            self.result_lbl.config(text="✅ All images reviewed.", fg="black", bg="white")
            return

        path = os.path.join(FOLDER_PATH, self.images[self.idx])
        if not is_file_stable(path):
            self.result_lbl.config(text="⏳ Skipping unstable file...", fg="orange", bg="white")
            return

        try:
            img = Image.open(path)
            img.thumbnail((420, 300), Image.ANTIALIAS)
            self.tkimg = ImageTk.PhotoImage(img)
            self.image_label.config(image=self.tkimg)
        except Exception as e:
            self.result_lbl.config(text=f"Load error: {e}", fg="red")
            return

        self.result_lbl.config(text="", bg="white")
        self.progress_lbl.config(text=f"🖼️ {self.idx+1} of {len(self.images)}")
        threading.Thread(target=self.analyze, args=(path,), daemon=True).start()

    def analyze(self, path):
        self.analyzing = True
        self.result_lbl.config(text="Analyzing...", fg="blue", bg="white")
        accept_output.off()
        reject_output.off()

        verdict = classify_image(path, self.sensitivity.get(), self.no_brand_var.get())
        color = "green" if verdict.upper().startswith("ACCEPT") else "red"
        self.result_lbl.config(text=verdict, fg=color, bg="white")

        if verdict.upper().startswith("ACCEPT"):
            accept_output.on()
            reject_output.off()
        else:
            reject_output.on()
            accept_output.off()

        self.analyzing = False

    def next_image(self):
        self.idx += 1
        self.display_image()

    def clear_server(self):
        for f in os.listdir(FOLDER_PATH):
            try:
                os.remove(os.path.join(FOLDER_PATH, f))
            except:
                pass
        self.images = []
        self.idx = 0
        self.image_label.config(image="")
        self.result_lbl.config(text="Cleared folder.", fg="black", bg="white")

    def update_repo(self):
        try:
            out = subprocess.check_output(["git", "-C", "/home/keyence/inspector", "pull"]).decode()
            self.result_lbl.config(text=f"🔁 Git updated: {output}"
{out}", fg="blue")
        except Exception as e:
            self.result_lbl.config(text=f"Git update failed: {e}", fg="red")

if __name__ == "__main__":
    root = tk.Tk()
    app = LidInspectorApp(root)
    root.mainloop()
