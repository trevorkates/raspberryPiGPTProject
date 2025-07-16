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
# Load environment variables from the .env file in the repo directory
load_dotenv("/home/keyence/inspector/.env")
openai.api_key = os.getenv("OPENAI_API_KEY")

# Folder where IV3 saves images via FTP
FOLDER_PATH = "/home/keyence/iv3_images"
POLL_INTERVAL = 5  # seconds between checks

# Example few-shot reference evaluations
REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT – Clean IML sticker, clear and centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT – White streaks are clearly visible in the print layer.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT – Shine is due to lighting reflection, not a defect."
}

def list_images():
    return sorted(
        f for f in os.listdir(FOLDER_PATH)
        if f.lower().endswith((".jpg", ".jpeg"))
    )

def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def classify_image(image_path, sensitivity):
    sensitivity_levels = {
        1: "Accept nearly everything, even with obvious imperfections.",
        2: "Accept mild streaks or small misprints. Reject only major flaws.",
        3: "Balanced – Reject unclear or misaligned branding or IML.",
        4: "Strict – Minor streaks or off-center prints may be REJECTED.",
        5: "Very strict – Any defect should result in REJECT."
    }
    system_prompt = (
        "You are an expert lid inspector. Classify whether a trash-can lid image "
        "should be ACCEPTED or REJECTED. Return exactly 'ACCEPT – reason' or 'REJECT – reason'. "
        f"Strictness {sensitivity}/5: {sensitivity_levels[sensitivity]}"
    )
    messages = [{"role": "system", "content": system_prompt}]
    for url, expl in REFERENCE_EXAMPLES.items():
        messages.append({"role": "user", "content": f"{expl} Image: {url}"})
    b64 = encode_image(image_path)
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": "Now evaluate this image:"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}} 
        ]
    })
    resp = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=messages
    )
    return resp.choices[0].message.content.strip()

class LidInspectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🗑️ Trash Lid Inspector")
        self.root.geometry("800x650")
        self.root.configure(bg="white")

        # UI widgets
        self.image_label = tk.Label(root, bg="white")
        self.result_label = tk.Label(root, font=("Helvetica", 14), bg="white")
        self.slider_label = tk.Label(root, text="Strictness (1–5):", bg="white")
        self.sensitivity_slider = tk.Scale(root, from_=1, to=5, orient="horizontal",
                                           command=lambda _: self.display_image(force=True), bg="white")
        self.start_button = tk.Button(root, text="Start Inspection", command=self.start_inspection)
        self.next_button = tk.Button(root, text="Next Image", command=self.next_image)

        # layout
        self.start_button.pack(pady=20)
        self.image_label.pack(pady=10)
        self.result_label.pack(pady=8)
        self.slider_label.pack()
        self.sensitivity_slider.set(3)
        self.sensitivity_slider.pack(pady=5)
        self.next_button.pack(pady=10)

        # internal state
        self.image_list = []
        self.index = 0
        self.refresh_thread = threading.Thread(target=self.watch_folder, daemon=True)

    def start_inspection(self):
        self.start_button.pack_forget()
        self.image_list = list_images()
        if self.image_list:
            self.display_image()
        self.refresh_thread.start()

    def watch_folder(self):
        known = set(self.image_list)
        while True:
            current = set(list_images())
            new = sorted(current - known)
            if new:
                self.image_list.extend(new)
                self.index = len(self.image_list) - len(new)
                self.display_image()
                known = current
            time.sleep(POLL_INTERVAL)

    def display_image(self, force=False):
        if self.index >= len(self.image_list):
            self.result_label.config(text="🎉 All images reviewed.")
            return
        path = os.path.join(FOLDER_PATH, self.image_list[self.index])
        try:
            img = Image.open(path)
            img.thumbnail((600, 400))
            self.tk_img = ImageTk.PhotoImage(img)
            self.image_label.config(image=self.tk_img)
            self.result_label.config(text="")
        except Exception as e:
            self.result_label.config(fg="red", text=f"Load error: {e}")
            return

        threading.Thread(target=self.analyze_image, args=(path,), daemon=True).start()

    def analyze_image(self, path):
        self.result_label.config(fg="orange", text="Analyzing…")
        try:
            dec = classify_image(path, self.sensitivity_slider.get())
            color = "green" if dec.upper().startswith("ACCEPT") else "red"
            self.result_label.config(fg=color, text=dec)
        except Exception as e:
            self.result_label.config(fg="red", text=f"Error: {e}")

    def next_image(self):
        self.index += 1
        self.display_image()

if __name__ == "__main__":
    root = tk.Tk()
    app = LidInspectorApp(root)
    root.mainloop()
