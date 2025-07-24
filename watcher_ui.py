#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import threading
import argparse
import base64
from queue import Queue

import tkinter as tk
from PIL import Image, ImageTk, ImageEnhance
from gpiozero import OutputDevice

import openai
from dotenv import load_dotenv
from io import BytesIO

from pymodbus.server.sync import StartTcpServer
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock

# --- CONFIG & COMMAND-LINE ARGS -----------------------------------
parser = argparse.ArgumentParser(description="Trash-Lid Inspector UI")
parser.add_argument("--folder",      default="/home/keyence/iv3_images")
parser.add_argument("--poll-int",    type=float, default=1.5)
# Switch default back to 502 so UR10 can connect without port override
parser.add_argument("--modbus-port", type=int,   default=502)
parser.add_argument("--accept-pin",  type=int,   default=19)
parser.add_argument("--reject-pin",  type=int,   default=20)
args = parser.parse_args()

FOLDER_PATH   = args.folder
POLL_INTERVAL = args.poll_int
MODBUS_PORT   = args.modbus_port

# Load & verify OpenAI key
load_dotenv("/home/keyence/inspector/.env")
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise RuntimeError("Missing OPENAI_API_KEY — please set it in your .env")

# Strictness guidance for each level
LEVEL_GUIDANCE = {
    1: "Accept almost everything; only reject truly broken lids (massive print dropout, huge holes).",
    2: "Accept minor print or placement issues; reject moderate flaws like small streaks or light scratches.",
    3: "Balanced: readability and centering are key; reject if branding is blurry, misaligned, or partially missing.",
    4: "Strict: reject even subtle ink inconsistencies, small misalignments, or any visible print defect.",
    5: "Very strict: only perfect lids pass; reject for any minor imperfection."
}

REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT - Clean IML sticker, clear and centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT - White streaks are clearly visible in the print layer.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT - Shine is due to lighting reflection, not a defect."
}

# --- MODBUS SERVER SETUP -------------------------------------------
store = ModbusSlaveContext(
    co=ModbusSequentialDataBlock(0, [0]*2)  # two coils: accept, reject
)
modbus_ctx = ModbusServerContext(slaves=store, single=True)

def run_modbus():
    try:
        StartTcpServer(modbus_ctx, address=("0.0.0.0", MODBUS_PORT))
    except PermissionError as e:
        print("Modbus bind failed:", e)
        fallback = 1502
        print(f"Retrying on port {fallback}")
        StartTcpServer(modbus_ctx, address=("0.0.0.0", fallback))

threading.Thread(target=run_modbus, daemon=True).start()

# --- UTILITY FUNCTIONS ---------------------------------------------
def list_images():
    imgs = [
        f for f in os.listdir(FOLDER_PATH)
        if f.lower().endswith((".jpg", ".jpeg"))
    ]
    return sorted(imgs, key=lambda f: int(os.path.splitext(f)[0]))


def clean_jpeg(path):
    try:
        with Image.open(path) as img:
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(1.2)
            with BytesIO() as buffer:
                img.save(buffer, format="JPEG", quality=90)
                return base64.b64encode(buffer.getvalue()).decode()
    except Exception as e:
        print(f"JPEG cleanup error: {e}")
        return None


def classify_image(path, sensitivity, no_brand_mode):
    level_text = LEVEL_GUIDANCE[sensitivity]
    focus = ("Ignore branding—only evaluate surface quality and color consistency." if no_brand_mode else level_text)

    system_prompt = (
        "You are a veteran factory QA inspector examining a single top-down photo of a plastic trash-can lid.  "
        "Treat this image as if you held it in your hand: look for hidden mold flash, raised burrs, sink marks, surface scratches, dents, misaligned IML or branding, faded or over-inked text, holes, or color streaks.  "
        f"At strictness level {sensitivity}/5, apply this: {focus}  "
        "Lighting glare and minor cosmetic variation are acceptable only if they do not obscure branding or structural defects.  "
        "Then choose exactly one of these, with no asterisks or markdown:\n"
        "- ACCEPT - reason (Confidence: XX%)\n"
        "- REJECT - reason (Confidence: XX%)\n"
        "Do not say you cannot evaluate. Confidence must be between 90% and 100%."
    )
    messages = [{"role":"system","content":system_prompt}]
    if not no_brand_mode:
        for url, example in REFERENCE_EXAMPLES.items():
            messages.append({"role":"user","content":f"{example} Image: {url}"})
    b64 = clean_jpeg(path)
    if not b64:
        return "ERROR: cleanup failed"
    messages.append({"role":"user","content":f"Now evaluate this image:\n![](data:image/jpeg;base64,{b64})"})
    resp = openai.ChatCompletion.create(model="gpt-4o", messages=messages)
    return resp.choices[0].message.content.strip()

# --- MAIN APPLICATION CLASS ----------------------------------------
class LidInspectorApp:
    def __init__(self, root):
        self.root = root
        self.accept_count = 0
        self.reject_count = 0
        self.modbus_ctx = modbus_ctx

        root.title("CM1 Lid Inspector")
        root.geometry("800x600")
        root.configure(bg="white")

        # Layout frames
        container = tk.Frame(root, bg="white")
        container.pack(fill="both", expand=True, padx=10, pady=10)

        self.left = tk.Frame(container, bg="white", width=400, height=600)
        self.right = tk.Frame(container, bg="white", width=380, height=600)
        self.left.pack(side="left", fill="both")
        self.right.pack(side="right", fill="y")
        self.left.pack_propagate(False)
        self.right.pack_propagate(False)

        # Image display
        self.image_label = tk.Label(self.left, bg="white")
        self.image_label.pack(fill="both", expand=True)

        # Top controls & counters
        topbar = tk.Frame(self.right, bg="white")
        topbar.pack(fill="x", pady=(0,10))
        row = tk.Frame(topbar, bg="white"); row.pack(anchor="center")

        # Logo
        logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            img.thumbnail((100,100), Image.ANTIALIAS)
            self.logo_tk = ImageTk.PhotoImage(img)
            tk.Label(row, image=self.logo_tk, bg="white").pack(side="left", padx=(0,20))

        # Counters
        cf = tk.Frame(row, bg="white"); cf.pack(side="left", anchor="n")
        self.accept_label = tk.Label(cf, text="Accepted: 0", font=("Helvetica",12), fg="green", bg="white")
        self.reject_label = tk.Label(cf, text="Rejected: 0", font=("Helvetica",12), fg="red", bg="white")
        self.accept_label.pack(anchor="w"); self.reject_label.pack(anchor="w")

        # Controls
        self.start_btn = tk.Button(self.right, text="Start Inspection", command=self.start_inspection,
                                   bg="#4CAF50", fg="white", font=("Helvetica",12,"bold"), height=2)
        self.level_names = ["Lenient","Relaxed","Balanced","Strict","Very Strict"]
        self.level_var = tk.StringVar(value=self.level_names[1])
        self.sensitivity_var = tk.IntVar(value=2)
        self.level_menu = tk.OptionMenu(self.right, self.level_var, *self.level_names, command=self.on_level_change)
        self.level_menu.config(font=("Helvetica",14))
        self.no_brand_var = tk.BooleanVar(value=False)
        self.no_brand_cb = tk.Checkbutton(self.right, text="No Brand/IML Mode", variable=self.no_brand_var,
                                          bg="lightgrey", width=20, command=lambda: self.display_image(force=True))
        self.result_lbl  = tk.Label(self.right, font=("Helvetica",14), wraplength=260, justify="left", bg="white")
        self.next_btn    = tk.Button(self.right, text="Next Image", command=self.next_image)
        self.clear_srv_btn = tk.Button(self.right, text="Clear Server Photos", command=self.clear_server)

        for w in (self.start_btn, self.level_menu, self.no_brand_cb,
                  self.result_lbl, self.next_btn, self.clear_srv_btn):
            w.pack(pady=6, fill="x")

        # Internal state
        self.seen   = set()
        self.images = []
        self.idx    = 0

        # Threads
        self.work_q = Queue()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self.watch_folder, daemon=True).start()

        # Hardware outputs
        self.accept_output = OutputDevice(args.accept_pin, active_high=True, initial_value=False)
        self.reject_output = OutputDevice(args.reject_pin, active_high=True, initial_value=False)

        # Auto-start inspection so images load immediately
        self.start_inspection()

    def wait_for_stable(self, path, interval=1.0, retries=3):
        last = -1
        for _ in range(retries):
            try:
                sz = os.path.getsize(path)
                mt = os.path.getmtime(path)
            except FileNotFoundError:
                return False
            if sz == last and (time.time() - mt) > interval:
                return True
            last = sz
            time.sleep(interval)
        return False

    def _worker(self):
        while True:
            path = self.work_q.get()
            self.analyze(path)
            self.work_q.task_done()

    def start_inspection(self):
        self.start_btn.pack_forget()
        self.images = list_images()
        self.seen   = set(self.images)
        self.idx    = 0
        if self.images:
            self.display_image()

    def watch_folder(self):
        while True:
            current = set(list_images())
            new     = current - self.seen
            for fname in sorted(new, key=lambda f: int(os.path.splitext(f)[0])):
                full = os.path.join(FOLDER_PATH, fname)
                if self.wait_for_stable(full, interval=POLL_INTERVAL/2, retries=4):
                    self.work_q.put(full)
            self.seen = current
            time.sleep(POLL_INTERVAL)

    def display_image(self, force=False):
        if self.idx >= len(self.images):
            self.result_lbl.config(text="All images reviewed.", fg="black")
            return
        path = os.path.join(FOLDER_PATH, self.images[self.idx])
        if not self.wait_for_stable(path, interval=POLL_INTERVAL) and not force:
            self.result_lbl.config(fg="red", text="Skipping unstable file. Will retry.")
            self.root.after(int(POLL_INTERVAL*1000), lambda: self.display_image(force=True))
            return
        try:
            img = Image.open(path)
            img.thumbnail((400,300), Image.ANTIALIAS)
            self.tkimg = ImageTk.PhotoImage(img)
            self.image_label.config(image=self.tkimg)
            self.result_lbl.config(text="", fg="black")
        except Exception as e:
            self.result_lbl.config(fg="red", text=f"Load error: {e}")
            return
        self.work_q.put(path)

    def analyze(self, path):
        # Show analyzing
        self.root.after(0, lambda: self.result_lbl.config(fg="orange", text="Analyzing…"))
        # Reset coils
        self.modbus_ctx[0].setValues(1, 0, [0,0])
        # Classify
        verdict = classify_image(path, self.sensitivity_var.get(), self.no_brand_var.get())
        bit0 = 1 if verdict.startswith("ACCEPT") else 0
        bit1 = 1 if verdict.startswith("REJECT") else 0
        self.modbus_ctx[0].setValues(1, 0, [bit0, bit1])
        # Update UI & hardware
        def finish_ui():
            color = "green" if bit0 else "red"
            self.result_lbl.config(fg=color, text=verdict)
            if bit0:
                self.accept_count += 1
                self.accept_label.config(text=f"Accepted: {self.accept_count}")
            else:
                self.reject_count += 1
                self.reject_label.config(text=f"Rejected: {self.reject_count}")
            self.accept_output.value = bool(bit0)
            self.reject_output.value = bool(bit1)
        self.root.after(0, finish_ui)

    def next_image(self):
        self.idx += 1
        self.display_image()

    def on_level_change(self, choice):
        idx = self.level_names.index(choice)
        self.sensitivity_var.set(idx+1)
        self.display_image(force=True)

    def clear_server(self):
        for fname in os.listdir(FOLDER_PATH):
            try:
                os.remove(os.path.join(FOLDER_PATH, fname))
            except Exception as e:
                print(f"Error deleting {fname}: {e}")
        self.images = []
        self.seen   = set()
        self.idx    = 0
        self.image_label.config(image="")
        self.result_lbl.config(text="Server cleared.", fg="black")
        self.accept_count = 0
        self.reject_count = 0
        self.accept_label.config(text="Accepted: 0")
        self.reject_label.config(text="Rejected: 0")

if __name__ == "__main__":
    root = tk.Tk()
    app = LidInspectorApp(root)
    root.mainloop()
