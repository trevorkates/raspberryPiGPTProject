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
    raise RuntimeError("Missing OPENAI_API_KEY — set in your .env file.")

# --- MODBUS SERVER SETUP -------------------------------------------
store = ModbusSlaveContext(co=ModbusSequentialDataBlock(0, [0]*2))  # coils: accept, reject
modbus_ctx = ModbusServerContext(slaves=store, single=True)

def run_modbus():
    try:
        StartTcpServer(modbus_ctx, address=("0.0.0.0", MODBUS_PORT))
    except PermissionError:
        StartTcpServer(modbus_ctx, address=("0.0.0.0", 1502))
threading.Thread(target=run_modbus, daemon=True).start()

# --- UTILITY FUNCTIONS ---------------------------------------------
def list_images():
    files = [f for f in os.listdir(FOLDER_PATH) if f.lower().endswith((".jpg",".jpeg"))]
    return sorted(files, key=lambda f: int(os.path.splitext(f)[0]))


def clean_jpeg(path):
    try:
        with Image.open(path) as img:
            img = ImageEnhance.Brightness(img).enhance(1.2)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def classify_image(path, sensitivity, no_brand_mode):
    # Build a concise prompt to minimize tokens
    system_prompt = (
        f"You are a QA inspector. Strictness {sensitivity}/5. "
        "Inspect the image for any defects. Respond exactly with 'ACCEPT' or 'REJECT'."
    )
    b64 = clean_jpeg(path)
    if not b64:
        return "ERROR: cleanup failed"
    messages = [
        {"role":"system","content":system_prompt},
        {"role":"user","content":f"Image:\n![](data:image/jpeg;base64,{b64})"}
    ]
    resp = openai.ChatCompletion.create(model="gpt-4o-mini", messages=messages)
    return resp.choices[0].message.content.strip().upper()

# --- MAIN APPLICATION CLASS ----------------------------------------
class LidInspectorApp:
    def __init__(self, root):
        self.root = root
        self.accept_count = 0
        self.reject_count = 0
        self.modbus_ctx = modbus_ctx
        root.title("Lid Inspector")
        root.geometry("800x600")

        self.left = tk.Frame(root, width=400, height=600)
        self.left.pack(side="left", fill="both", expand=True)
        self.right = tk.Frame(root, width=380)
        self.right.pack(side="right", fill="y")

        self.image_label = tk.Label(self.left)
        self.image_label.pack(expand=True)
        self.result_lbl = tk.Label(self.right, text="", font=("Arial",14))
        self.result_lbl.pack(pady=10)

        self.start_btn = tk.Button(self.right, text="Start", command=self.start)
        self.start_btn.pack(fill="x", pady=5)
        self.next_btn = tk.Button(self.right, text="Next", command=self.next_image)
        self.next_btn.pack(fill="x", pady=5)

        self.seen = set()
        self.images = []
        self.idx = 0

        self.work_q = Queue()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self.watch_folder, daemon=True).start()

    def watch_folder(self):
        while True:
            current = set(list_images())
            new = current - self.seen
            for f in sorted(new, key=lambda x: int(os.path.splitext(x)[0])):
                p = os.path.join(FOLDER_PATH, f)
                if self._is_stable(p):
                    self.work_q.put(p)
            self.seen = current
            time.sleep(POLL_INTERVAL)

    def _is_stable(self, path):
        try:
            sz1 = os.path.getsize(path)
            time.sleep(0.5)
            sz2 = os.path.getsize(path)
            return sz1 == sz2
        except:
            return False

    def clean_display(self, path):
        img = Image.open(path)
        img.thumbnail((400,300))
        self.tkimg = ImageTk.PhotoImage(img)
        self.image_label.config(image=self.tkimg)

    def display_image(self):
        if self.idx >= len(self.images):
            self.result_lbl.config(text="Done.")
            return
        path = os.path.join(FOLDER_PATH, self.images[self.idx])
        self.clean_display(path)

    def _worker(self):
        while True:
            p = self.work_q.get()
            verdict = classify_image(p, 3, False)
            bit0 = 1 if verdict == "ACCEPT" else 0
            self.modbus_ctx[0].setValues(1,0,[bit0,1-bit0])
            self.root.after(0, lambda v=verdict: self.result_lbl.config(text=v))
            self.images.append(os.path.basename(p))
            self.work_q.task_done()

    def start(self):
        self.images = list_images()
        self.idx = 0
        if self.images:
            self.display_image()

    def next_image(self):
        self.idx += 1
        self.display_image()

if __name__ == "__main__":
    root = tk.Tk()
    app = LidInspectorApp(root)
    root.mainloop()
