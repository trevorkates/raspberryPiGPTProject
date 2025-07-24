#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import base64
import io
import threading
import queue
import tkinter as tk
from PIL import Image, ImageTk
import cv2
import numpy as np
import openai
from openai.error import RateLimitError
from dotenv import load_dotenv
from pymodbus.server.sync import StartTcpServer
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock

# --- CONFIG --------------------------------------------------------
load_dotenv("/home/keyence/inspector/.env")
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise RuntimeError("Missing OPENAI_API_KEY")

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
FOLDER_PATH   = "/home/keyence/iv3_images"
POLL_INTERVAL = 5  # seconds between folder scans
COIL_ADDRESS  = 1  # Modbus coil address for ACCEPT/REJECT signal
MODEL_NAME    = "gpt-4o-mini"

# Strictness options mapping (display text -> level)
STRICTNESS_OPTIONS = {
    "Very Lenient": 1,
    "Lenient":      2,
    "Balanced":     3,
    "Strict":       4,
    "Very Strict":  5
}

LEVEL_GUIDANCE = {
    1: "Accept almost everything; only reject truly broken lids.",
    2: "Accept minor print or placement issues; reject moderate flaws like small streaks or scratches.",
    3: "Balanced: readability and centering are key; reject if branding is blurry or misaligned.",
    4: "Strict: reject even subtle ink inconsistencies or small misalignments.",
    5: "Very strict: only perfect lids pass; reject any minor imperfection."
}

REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT - Clean IML sticker, centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT - White streaks in print layer.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT - Lighting shine only, no defect."
}

# --- SETUP MODBUS --------------------------------------------------
store = ModbusSlaveContext(
    co=ModbusSequentialDataBlock(0, [0]*100),
)
modbus_ctx = ModbusServerContext(slaves=store, single=True)

def start_modbus_server():
    StartTcpServer(modbus_ctx, address=("0.0.0.0", 502))

threading.Thread(target=start_modbus_server, daemon=True).start()

# --- RATE-LIMIT HANDLING -------------------------------------------
def chat_completion_with_retry(**kwargs):
    delay = 1
    while True:
        try:
            return openai.ChatCompletion.create(**kwargs)
        except RateLimitError:
            time.sleep(delay)
            delay = min(delay * 2, 20)

# --- APPLICATION ---------------------------------------------------
class InspectorApp:
    def __init__(self, root):
        self.root = root
        root.title("CM1 Lid Inspector")
        root.geometry("800x600")
        root.configure(bg="white")

        # State
        self._processed   = set()
        self._queue       = queue.Queue()
        self.current_path = None
        self.show_cleaned = False
        self.accept_count = 0
        self.reject_count = 0
        self.clean_cache  = {}  # path -> PhotoImage

        # Layout
        container = tk.Frame(root, bg="white")
        container.pack(fill="both", expand=True, padx=10, pady=10)
        self.left = tk.Frame(container, bg="white", width=400, height=600)
        self.right = tk.Frame(container, bg="white", width=380, height=600)
        self.left.pack(side="left", fill="both")
        self.right.pack(side="right", fill="y")
        self.left.pack_propagate(False)
        self.right.pack_propagate(False)

        # Image display + toggle
        self.image_label = tk.Label(self.left, bg="white")
        self.image_label.pack(fill="both", expand=True, padx=5, pady=5)
        self.toggle_btn = tk.Button(self.left, text="Show Cleaned", command=self._toggle_view)
        self.toggle_btn.pack(pady=5)

        # Topbar: logo + counters
        topbar = tk.Frame(self.right, bg="white")
        topbar.pack(fill="x", pady=(0,10))
        logo_path = os.path.join(BASE_DIR, "logo.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path); img.thumbnail((100,100))
            self.logo_img = ImageTk.PhotoImage(img)
            tk.Label(topbar, image=self.logo_img, bg="white").pack(side="left", padx=(0,20))
        cnt_frame = tk.Frame(topbar, bg="white")
        cnt_frame.pack(side="left")
        self.accept_label = tk.Label(cnt_frame, text="Accepted: 0",
                                     font=("Helvetica",12), fg="green", bg="white")
        self.reject_label = tk.Label(cnt_frame, text="Rejected: 0",
                                     font=("Helvetica",12), fg="red", bg="white")
        self.accept_label.pack(); self.reject_label.pack()

        # Controls: strictness & no-brand
        tk.Label(self.right, text="Strictness:", bg="white",
                 font=("Helvetica",12)).pack(pady=(10,0), fill="x")
        self.sensitivity_var = tk.StringVar(value="Balanced")
        opts = list(STRICTNESS_OPTIONS.keys())
        self.level_menu = tk.OptionMenu(self.right, self.sensitivity_var, *opts)
        self.level_menu.config(font=("Helvetica",14)); self.level_menu.pack(pady=5, fill="x")
        self.no_brand_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self.right, text="No IML/Brand Mode",
                       variable=self.no_brand_var, bg="white").pack(pady=5, fill="x")

        # Results
        self.result_lbl = tk.Label(self.right, text="Waiting for images...",
                                   font=("Helvetica",14), wraplength=260,
                                   justify="left", bg="white")
        self.result_lbl.pack(pady=5, fill="x")
        self.detail_lbl = tk.Label(self.right, text="", font=("Helvetica",12),
                                   wraplength=260, justify="left", bg="white")
        self.detail_lbl.pack(pady=5, fill="x")

        # Clear server button
        tk.Button(self.right, text="Clear Server Photos",
                  command=self.clear_server, bg="white").pack(pady=10, fill="x")

        # Start worker
        threading.Thread(target=self._worker, daemon=True).start()

    def clear_server(self):
        self._processed.clear()
        with self._queue.mutex:
            self._queue.queue.clear()
        self.accept_count = self.reject_count = 0
        self.accept_label.config(text="Accepted: 0")
        self.reject_label.config(text="Rejected: 0")
        self.result_lbl.config(text="Waiting for images...", fg="black")
        self.detail_lbl.config(text="")

    def _toggle_view(self):
        self.show_cleaned = not self.show_cleaned
        if self.current_path:
            if self.show_cleaned:
                # show cleaned if cached or trigger cleaning
                if self.current_path in self.clean_cache:
                    self._set_image(self.clean_cache[self.current_path])
                else:
                    threading.Thread(target=self._async_clean, args=(self.current_path,), daemon=True).start()
                self.toggle_btn.config(text="Show Raw")
            else:
                # show raw
                raw_img = Image.open(self.current_path)
                raw_img.thumbnail((400,400))
                photo = ImageTk.PhotoImage(raw_img)
                self._set_image(photo)
                self.toggle_btn.config(text="Show Cleaned")

    def _set_image(self, photo):
        self.image_label.config(image=photo)
        self.current_photo = photo

    def _sort_key(self, fname):
        name, _ = os.path.splitext(fname)
        try: return int(name)
        except: return float('inf')

    def _is_file_stable(self, path, wait=1.0):
        try:
            sz = os.path.getsize(path); time.sleep(wait)
            return sz == os.path.getsize(path)
        except: return False

    def _worker(self):
        while True:
            try:
                files = [f for f in os.listdir(FOLDER_PATH)
                         if f.lower().endswith(('.jpg','.jpeg','.png'))]
                for fname in sorted(files, key=self._sort_key):
                    if fname in self._processed: continue
                    path = os.path.join(FOLDER_PATH, fname)
                    if self._is_file_stable(path):
                        self._queue.put(path)
                        self._processed.add(fname)
                while not self._queue.empty():
                    self._process_file(self._queue.get())
                time.sleep(POLL_INTERVAL)
            except Exception as e:
                print("Worker error:", e); time.sleep(POLL_INTERVAL)

    def _process_file(self, path):
        self.current_path = path
        # display raw immediately, then start cleaning
        self.root.after(0, lambda p=path: self._display_raw(p))
        threading.Thread(target=self._async_clean, args=(path,), daemon=True).start()
        # analyze in background
        lvl_word = self.sensitivity_var.get()
        sensitivity = STRICTNESS_OPTIONS.get(lvl_word, 3)
        no_brand = self.no_brand_var.get()
        result, detail = self._analyze_image(path, sensitivity, no_brand)
        # update UI and counters
        self.root.after(0, lambda r=result, d=detail: self._update_result(r, d))
        if result == "ACCEPT":
            self.accept_count += 1
            self.accept_label.config(text=f"Accepted: {self.accept_count}")
        else:
            self.reject_count += 1
            self.reject_label.config(text=f"Rejected: {self.reject_count}")
        # send modbus coil
        coil_val = 1 if result=="ACCEPT" else 0
        modbus_ctx[0].setValues(1, COIL_ADDRESS, [coil_val])

    def _display_raw(self, path):
        try:
            img = Image.open(path)
            img.thumbnail((400,400))
            photo = ImageTk.PhotoImage(img)
            self._set_image(photo)
        except Exception as e:
            print("Display raw error:", e)

    def _async_clean(self, path):
        try:
            # downsize then remove glare
            pil = Image.open(path)
            pil.thumbnail((400,400))
            arr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
            hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)
            v = hsv[:,:,2]
            _, mask = cv2.threshold(v, 240, 255, cv2.THRESH_BINARY)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7,7))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            inpainted = cv2.inpaint(arr, mask, 5, cv2.INPAINT_TELEA)
            rgb = cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB)
            clean_pil = Image.fromarray(rgb)
            clean_pil.thumbnail((400,400))
            photo = ImageTk.PhotoImage(clean_pil)
            self.clean_cache[path] = photo
            if self.current_path == path and self.show_cleaned:
                self.root.after(0, lambda: self._set_image(photo))
        except Exception as e:
            print("Async clean error:", e)

    def _update_result(self, result, detail):
        color = "green" if result=="ACCEPT" else "red"
        self.result_lbl.config(text=result, fg=color)
        self.detail_lbl.config(text=detail)

    def _analyze_image(self, path, sensitivity, no_brand):
        try:
            # preprocess for glare in analysis
            pil = Image.open(path); pil.thumbnail((400,400))
            buf = io.BytesIO(); pil.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            data_uri = f"data:image/jpeg;base64,{b64}"
            glare_text = "Ignore any small specular highlights from lighting glare."
            if no_brand:
                focus = glare_text + " Ignore branding—only evaluate surface quality."
            else:
                focus = glare_text + " " + LEVEL_GUIDANCE[sensitivity]
            system_prompt = (
                "You are a QA inspector. "
                f"At strictness level {sensitivity}/5, apply: {focus} "
                "Respond 'ACCEPT - reason (Confidence: XX%)' or 'REJECT - reason (Confidence: XX%)'."
            )
            msgs = [{"role":"system","content":system_prompt}]
            if not no_brand:
                for url, ex in REFERENCE_EXAMPLES.items():
                    msgs.append({"role":"user","content":f"{ex} Image: {url}"})
            msgs.append({"role":"user","content":"Here is the image: " + data_uri})
            resp = chat_completion_with_retry(model=MODEL_NAME, messages=msgs)
            text = resp.choices[0].message.content.strip()
            parts = text.split(" ",1)
            return parts[0].upper(), parts[1] if len(parts)>1 else ""
        except Exception as e:
            print("Analysis error:", e)
            return "ERROR", str(e)

if __name__ == "__main__":
    root = tk.Tk()
    app = InspectorApp(root)
    root.mainloop()
