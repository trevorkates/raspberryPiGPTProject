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
from gpiozero import OutputDevice
import openai
from dotenv import load_dotenv
from pymodbus.server.sync import StartTcpServer
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock

# --- CONFIG --------------------------------------------------------
load_dotenv("/home/keyence/inspector/.env")
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise RuntimeError("Missing OPENAI_API_KEY")

FOLDER_PATH   = "/home/keyence/iv3_images"
POLL_INTERVAL = 3  # seconds between folder scans
COIL_ADDRESS  = 1  # Modbus coil address for ACCEPT/REJECT signal
MODEL_NAME    = "gpt-4o-mini"

# Strictness guidance texts
LEVEL_GUIDANCE = {
    1: "Accept almost everything; only reject truly broken lids (massive print dropout, huge holes).",
    2: "Accept minor print or placement issues; reject moderate flaws like small streaks or light scratches.",
    3: "Balanced: readability and centering are key; reject if branding is blurry, misaligned, or partially missing.",
    4: "Strict: reject even subtle ink inconsistencies, small misalignments, or any visible print defect.",
    5: "Very strict: only perfect lids pass; reject for any minor imperfection."
}

# Few-shot examples for reference
REFERENCE_EXAMPLES = {
    "https://i.imgur.com/xXbGo0g.jpeg": "ACCEPT - Clean IML sticker, clear and centered branding.",
    "https://i.imgur.com/NDmSVPz.jpeg": "REJECT - White streaks are clearly visible in the print layer.",
    "https://i.imgur.com/12zH9va.jpeg": "ACCEPT - Shine is due to lighting reflection, not a defect."
}

# --- SETUP MODBUS --------------------------------------------------
store = ModbusSlaveContext(
    co=ModbusSequentialDataBlock(0, [0]*100),
)
modbus_ctx = ModbusServerContext(slaves=store, single=True)

def start_modbus_server():
    StartTcpServer(modbus_ctx, address=("0.0.0.0", 502))

threading.Thread(target=start_modbus_server, daemon=True).start()

# --- GLARE REMOVAL FUNCTION ----------------------------------------
def remove_glare(path):
    """Remove specular highlights using OpenCV inpainting."""
    img_bgr = cv2.imread(path)
    if img_bgr is None:
        raise FileNotFoundError(f"Cannot read image: {path}")
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    _, mask = cv2.threshold(v, 240, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7,7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    inpainted = cv2.inpaint(img_bgr, mask, 5, cv2.INPAINT_TELEA)
    img_rgb = cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB)
    return Image.fromarray(img_rgb)

# --- APPLICATION --------------------------------------------------
class InspectorApp:
    def __init__(self, root):
        self.root = root
        root.title("CM1 Lid Inspector")
        root.geometry("800x600")
        root.configure(bg="white")

        # State
        self._processed = set()
        self._queue = queue.Queue()
        self.current_path = None
        self.show_cleaned = False
        self.accept_count = 0
        self.reject_count = 0

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
        self.image_label.pack(fill="both", expand=True, padx=5, pady=5)
        # Toggle button
        self.toggle_btn = tk.Button(self.left, text="Show Cleaned", command=self._toggle_view)
        self.toggle_btn.pack(pady=5)

        # Top bar with logo and counters
        topbar = tk.Frame(self.right, bg="white")
        topbar.pack(fill="x", pady=(0,10))
        if os.path.exists("logo.png"):
            img = Image.open("logo.png"); img.thumbnail((100,100))
            self.logo_img = ImageTk.PhotoImage(img)
            tk.Label(topbar, image=self.logo_img, bg="white").pack(side="left", padx=(0,20))
        cnt_frame = tk.Frame(topbar, bg="white")
        cnt_frame.pack(side="left")
        self.accept_label = tk.Label(cnt_frame, text="Accepted: 0", font=("Helvetica",12), fg="green", bg="white")
        self.reject_label = tk.Label(cnt_frame, text="Rejected: 0", font=("Helvetica",12), fg="red", bg="white")
        self.accept_label.pack()
        self.reject_label.pack()

        # Controls: strictness and no-brand
        tk.Label(self.right, text="Strictness:", bg="white", font=("Helvetica",12)).pack(pady=(10,0), fill="x")
        self.sensitivity_var = tk.IntVar(value=3)
        self.level_menu = tk.OptionMenu(self.right, self.sensitivity_var, *[1,2,3,4,5])
        self.level_menu.config(font=("Helvetica",14)); self.level_menu.pack(pady=5, fill="x")
        self.no_brand_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self.right, text="No IML/Brand Mode", variable=self.no_brand_var,
                       bg="white").pack(pady=5, fill="x")

        # Results display
        self.result_lbl = tk.Label(self.right, text="Waiting for images...", font=("Helvetica",14),
                                   wraplength=260, justify="left", bg="white")
        self.result_lbl.pack(pady=5, fill="x")
        self.detail_lbl = tk.Label(self.right, text="", font=("Helvetica",12),
                                   wraplength=260, justify="left", bg="white")
        self.detail_lbl.pack(pady=5, fill="x")

        # Clear server button
        tk.Button(self.right, text="Clear Server Photos", command=self.clear_server,
                  bg="white").pack(pady=10, fill="x")

        # Start worker
        threading.Thread(target=self._worker, daemon=True).start()

    def _toggle_view(self):
        """Toggle between raw and cleaned display of current image."""
        self.show_cleaned = not self.show_cleaned
        if self.current_path:
            self._display_image(self.current_path)
        self.toggle_btn.config(text="Show Raw" if self.show_cleaned else "Show Cleaned")

    def clear_server(self):
        """Reset state and UI."""
        self._processed.clear()
        with self._queue.mutex:
            self._queue.queue.clear()
        self.accept_count = self.reject_count = 0
        self.accept_label.config(text="Accepted: 0")
        self.reject_label.config(text="Rejected: 0")
        self.result_lbl.config(text="Waiting for images...", fg="black")
        self.detail_lbl.config(text="")

    def _is_file_stable(self, path, wait=1.0):
        try:
            initial = os.path.getsize(path)
            time.sleep(wait)
            return initial == os.path.getsize(path)
        except:
            return False

    def _worker(self):
        while True:
            try:
                for fname in sorted(os.listdir(FOLDER_PATH), key=lambda f: int(os.path.splitext(f)[0])):
                    if fname in self._processed: continue
                    path = os.path.join(FOLDER_PATH, fname)
                    if self._is_file_stable(path):
                        self._queue.put(path)
                        self._processed.add(fname)
                while not self._queue.empty():
                    path = self._queue.get()
                    self._process_file(path)
                time.sleep(POLL_INTERVAL)
            except Exception as e:
                print("Worker error:", e)
                time.sleep(POLL_INTERVAL)

    def _process_file(self, path):
        self.current_path = path
        # display raw or cleaned based on toggle
        self.root.after(0, lambda p=path: self._display_image(p))
        lvl = self.sensitivity_var.get()
        no_brand = self.no_brand_var.get()
        result, detail = self._analyze_image(path, lvl, no_brand)
        self.root.after(0, lambda r=result, d=detail: self._update_result(r, d))
        if result == "ACCEPT":
            self.accept_count += 1
            self.accept_label.config(text=f"Accepted: {self.accept_count}")
        else:
            self.reject_count += 1
            self.reject_label.config(text=f"Rejected: {self.reject_count}")
        coil_val = 1 if result=="ACCEPT" else 0
        modbus_ctx[0].setValues(1, COIL_ADDRESS, [coil_val])

    def _display_image(self, path):
        try:
            if self.show_cleaned:
                img = remove_glare(path)
            else:
                img = Image.open(path)
            img.thumbnail((400,400))
            self.img_tk = ImageTk.PhotoImage(img)
            self.image_label.config(image=self.img_tk)
        except Exception as e:
            print("Display error:", e)

    def _update_result(self, result, detail):
        color = "green" if result=="ACCEPT" else "red"
        self.result_lbl.config(text=result, fg=color)
        self.detail_lbl.config(text=detail)

    def _analyze_image(self, path, sensitivity, no_brand):
        try:
            # Read and optionally preprocess for glare
            cleaned = remove_glare(path)
            buf = io.BytesIO()
            cleaned.save(buf, format="JPEG")
            img_bytes = buf.getvalue()
            b64 = base64.b64encode(img_bytes).decode()
            data_uri = f"data:image/jpeg;base64,{b64}"

            # Build prompt with glare ignore
            glare_text = "Ignore any small specular highlights from lighting glare."
            if no_brand:
                focus = glare_text + " Ignore branding—only evaluate surface quality and color consistency."
            else:
                focus = glare_text + " " + LEVEL_GUIDANCE.get(sensitivity, LEVEL_GUIDANCE[3])

            system_prompt = (
                "You are a veteran factory QA inspector examining a single top-down photo of a plastic trash-can lid. "
                f"At strictness level {sensitivity}/5, apply this: {focus} "
                "Then respond with exactly 'ACCEPT - reason (Confidence: XX%)' or 'REJECT - reason (Confidence: XX%)'."
            )

            messages = [{"role":"system","content":system_prompt}]
            if not no_brand:
                for url, ex in REFERENCE_EXAMPLES.items():
                    messages.append({"role":"user","content":f"{ex} Image: {url}"})
            messages.append({"role":"user","content":"Here is the image to inspect: " + data_uri})

            resp = openai.ChatCompletion.create(model=MODEL_NAME, messages=messages)
            text = resp.choices[0].message.content.strip()
            parts = text.split(" ",1)
            result = parts[0].upper()
            detail = parts[1] if len(parts)>1 else ""
            return result, detail
        except Exception as e:
            print("Analysis error:", e)
            return "ERROR", str(e)

if __name__ == "__main__":
    root = tk.Tk()
    app = InspectorApp(root)
    root.mainloop()
