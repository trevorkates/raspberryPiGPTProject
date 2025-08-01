#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import base64
import threading
import tkinter as tk
from PIL import Image, ImageTk
from gpiozero import OutputDevice
import openai
from dotenv import load_dotenv
from io import BytesIO

from pymodbus.server.sync import StartTcpServer
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock

# --- CONFIG --------------------------------------------------------
load_dotenv("/home/keyence/inspector/.env")
openai.api_key = os.getenv("OPENAI_API_KEY")

FOLDER_PATH   = "/home/keyence/iv3_images"
POLL_INTERVAL = 1.5  # seconds between folder checks

# Ensure /results folder exists and is writable
results_dir = "/home/keyence/results"
if not os.path.exists(results_dir):
    os.makedirs(results_dir, exist_ok=True)
    try:
        os.system("sudo chown -R pi:pi /home/keyence/results")
    except Exception as e:
        print(f"⚠️ Could not change folder ownership: {e}")

accept_output = OutputDevice(19, active_high=True, initial_value=False)

# strictness guidance for each level - tightened lenient and relaxed
LEVEL_GUIDANCE = {
    1: "Reject only critical issues like holes, cracks, short shots, or missing plastic. Pass all cosmetic variation unless it would interfere with use. Ignore all surface marks, streaks, and glare.",
    2: "Reject for clear physical defects like deep dents, heavy flash, or print that's completely unreadable. Allow mild scuffing, slight print variation, or faint streaks. Do not reject for minor alignment or aesthetics.",
    3: "Reject only if branding is unreadable from 3 feet, flash is sharp or prominent, or labels are clearly misaligned. Acceptable lids may have slight scuffing, color shift, or off-center but complete print.",
    4: "Reject anything visually disruptive: off-center or overly dark/light branding, pitting, contamination, visible surface defects, or labels not cleanly applied. Lid should be visually clean and accurate on first glance.",
    5: "Only flawless parts should pass. Reject for any blemish, misprint, label variation, flash, streak, or surface irregularity. All defects matter."
}

REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT - Clean IML sticker, clear and centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT - White streaks are simply reflections, not defects.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT - Shine is due to lighting reflection, not a defect."
}

# Modbus setup
store = ModbusSlaveContext(di=ModbusSequentialDataBlock(0, [0, 0]))
modbus_ctx = ModbusServerContext(slaves=store, single=True)

def run_modbus():
    try:
        StartTcpServer(modbus_ctx, address=("0.0.0.0", 502))
    except Exception as e:
        print(f"Modbus server error: {e}")

threading.Thread(target=run_modbus, daemon=True).start()

# --- IMAGE UTILITIES ------------------------------------------------

def list_images():
    return sorted(
        f for f in os.listdir(FOLDER_PATH)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    )

def is_file_stable(path, wait_time=1.0):
    time.sleep(wait_time)
    try:
        size = os.path.getsize(path)
        time.sleep(0.5)
        new_size = os.path.getsize(path)
        mtime = os.path.getmtime(path)
        return size == new_size and (time.time() - mtime) > wait_time
    except FileNotFoundError:
        return False

# --- CLASSIFICATION ------------------------------------------------

def classify_image(path, sensitivity, no_brand_mode):
    # read raw image bytes for encoding
    try:
        with open(path, 'rb') as f:
            data = f.read()
        b64 = base64.b64encode(data).decode()
    except Exception as e:
        return f"Error: Unable to read image: {e}"

    # level guidance or override
    level_text = LEVEL_GUIDANCE.get(sensitivity, LEVEL_GUIDANCE[3])
    if no_brand_mode:
        focus = (
            "Use common sense: pass parts with small visual imperfections if they don’t affect use or brand image. "
            "Reject only if the issue is clearly visible or would cause confusion, damage, or rejection by a customer."
        )

    else:
        focus = level_text

        system_prompt = (
            "You are a trained quality inspector analyzing a top-down image of a plastic trash-can lid. "
            "Follow strict manufacturing inspection standards to determine whether the part passes. "
            "Use these defect rules:\n"
            "- FLASH: Reject if flash is visible on handles, lid edges, or around logos — especially if sharp or inconsistent.\n"
            "- BRANDING: Hot stamps or IML must match approved artwork. Reject if unreadable from 3 feet, misaligned, over-dark, faded, smeared, or incomplete.\n"
            "- LABELS: Reject if crooked, lifting, flaking, peeling, or not fully adhered.\n"
            "- SHORT SHOTS: Reject any signs of incomplete mold filling (holes, gaps, missing features).\n"
            "- AESTHETICS: Reject for pitting, surface contamination, excessive color streaking, or warping visible from 3 feet.\n"
            "- FOREIGN MATERIAL: Reject if contaminated with grease, dirt, or foreign matter.\n\n"
            "Parts should pass if they meet functional and visual expectations. If branding is readable, surfaces are generally uniform, and there are no major physical flaws, then the part is acceptable — even if it shows minor cosmetic variation such as light scuffing, off-center branding, or slight label shift. Do not reject parts that meet this standard.\n\n"
            f"At strictness level {sensitivity}/5, apply this guidance: {focus} "
            "Completely ignore glare — especially white streaks or shiny areas on dark plastic — they are not defects. "
            "Do not mention glare in your evaluation. "
            "Respond with exactly one choice, formatted like this (no extra text):\n"
            "ACCEPT - reason (Confidence: XX%)\n"
            "REJECT - reason (Confidence: XX%)"
        )

    messages = [{"role": "system", "content": system_prompt}]
    if not no_brand_mode:
        for url, example in REFERENCE_EXAMPLES.items():
            messages.append({"role": "user", "content": f"{example} Image: {url}"})

    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": "Now evaluate this image:"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}
        ]
    })

    resp = openai.ChatCompletion.create(model="gpt-4o", messages=messages)
    return resp.choices[0].message.content.strip()

# --- UI APPLICATION --------------------------------------------------

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

        # Top bar with logo and counters
        topbar = tk.Frame(self.right, bg="white")
        topbar.pack(fill="x", pady=(0, 10))

        logo_counter_row = tk.Frame(topbar, bg="white")
        logo_counter_row.pack(anchor="center")

        logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            img.thumbnail((100, 100), Image.ANTIALIAS)
            self.logo_tk = ImageTk.PhotoImage(img)
            tk.Label(logo_counter_row, image=self.logo_tk, bg="white").pack(side="left", padx=(0, 20))

        self.counter_frame = tk.Frame(logo_counter_row, bg="white")
        self.counter_frame.pack(side="left", anchor="n")

        self.accept_label = tk.Label(self.counter_frame, text="Accepted: 0", font=("Helvetica", 12), fg="green", bg="white")
        self.reject_label = tk.Label(self.counter_frame, text="Rejected: 0", font=("Helvetica", 12), fg="red", bg="white")
        self.accept_label.pack(anchor="w")
        self.reject_label.pack(anchor="w")

        self.start_btn = tk.Button(self.right, text="Start Inspection", command=self.start_inspection,
                                   bg="#4CAF50", fg="white", font=("Helvetica",12,"bold"), height=2)

        self.level_names = ["Lenient", "Relaxed", "Balanced", "Strict", "Very Strict"]
        self.level_var = tk.StringVar(value=self.level_names[1])
        self.sensitivity_var = tk.IntVar(value=2)

        self.level_menu = tk.OptionMenu(self.right, self.level_var, *self.level_names, command=self.on_level_change)
        self.level_menu.config(font=("Helvetica",14))

        self.no_brand_var = tk.BooleanVar(value=False)
        self.no_brand_cb = tk.Checkbutton(self.right, text="No Brand/IML Mode", variable=self.no_brand_var,
                                          bg="lightgrey", width=20, command=lambda: self.display_image(force=True))

        self.result_lbl = tk.Label(self.right, font=("Helvetica",14), wraplength=260, justify="left", bg="white")
        self.next_btn = tk.Button(self.right, text="Next Image", command=self.next_image)
        self.clear_srv_btn = tk.Button(self.right, text="Clear Server Photos", command=self.clear_server)

        for w in (self.start_btn, self.level_menu, self.no_brand_cb, self.result_lbl, self.next_btn, self.clear_srv_btn):
            w.pack(pady=6, fill="x")

        self.images = []
        self.idx = 0
        self.seen = set()
        self.analyzing = False
        self.poll_thr = threading.Thread(target=self.watch_folder, daemon=True)

        self.accept_count = 0
        self.reject_count = 0

    def start_inspection(self):
        self.start_btn.pack_forget()
        self.images = list_images()
        self.seen = set(self.images)
        if self.images:
            self.idx = 0
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
        if not is_file_stable(path, wait_time=POLL_INTERVAL) and not force:
            self.result_lbl.config(fg="red", text="Skipping unstable file. Will retry.")
            self.right.after(int(POLL_INTERVAL * 1000), lambda: self.display_image(force=True))
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
        modbus_ctx[0].setValues(2, 0, [0, 0])
        accept_output.off()
        try:
            lvl = self.sensitivity_var.get()
            verdict = classify_image(path, lvl, self.no_brand_var.get())

            bit0 = 1 if verdict.upper().startswith("ACCEPT") else 0
            bit1 = 1 if verdict.upper().startswith("REJECT") else 0
            modbus_ctx[0].setValues(2, 0, [bit0, bit1])

            color = "green" if bit0 else "red"
            self.result_lbl.config(fg=color, text=verdict)

            if bit0:
                self.accept_count += 1
                self.accept_label.config(text=f"Accepted: {self.accept_count}")
                accept_output.on()
            else:
                self.reject_count += 1
                self.reject_label.config(text=f"Rejected: {self.reject_count}")
                accept_output.off()

            # Save classification result to results folder
            # results_dir = "/home/keyence/results"
            os.makedirs(results_dir, exist_ok=True)

            verdict_label = "ACCEPT" if verdict.upper().startswith("ACCEPT") else "REJECT"
            result_filename = f"{os.path.splitext(os.path.basename(path))[0]}_{verdict_label}.txt"
            with open(os.path.join(results_dir, result_filename), "w") as f:
                f.write(verdict)

        except Exception as e:
            self.result_lbl.config(fg="red", text=f"Error: {e}")
            accept_output.off()
        finally:
            self.analyzing = False

    def next_image(self):
        self.idx += 1
        self.display_image()

    def on_level_change(self, choice):
        self.sensitivity_var.set(self.level_names.index(choice) + 1)
        self.display_image(force=True)

    def clear_server(self):
        for fname in os.listdir(FOLDER_PATH):
            try:
                os.remove(os.path.join(FOLDER_PATH, fname))
            except Exception as e:
                print(f"Error deleting {fname}: {e}")

        # results_dir = "/home/keyence/results"
        if os.path.exists(results_dir):
            for rname in os.listdir(results_dir):
                try:
                    os.remove(os.path.join(results_dir, rname))
                except Exception as e:
                    print(f"Error deleting result file {rname}: {e}")
                    
        self.images.clear()
        self.seen.clear()
        self.idx = 0
        self.image_label.config(image="")
        self.result_lbl.config(text="Server cleared - folder is now empty.", fg="black")
        self.accept_count = 0
        self.reject_count = 0
        self.accept_label.config(text="Accepted: 0")
        self.reject_label.config(text="Rejected: 0")

if __name__ == "__main__":
    root = tk.Tk()
    app = LidInspectorApp(root)
    root.mainloop()
