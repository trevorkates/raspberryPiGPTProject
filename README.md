# Lid Inspector
Summer 2025 internship project that uses a vision camera, raspberry pi 3, and chat gpt-4o api to perform quality inspections on 35 gallon trash can lids.
All the main code is in the watcher_ui.py file. Really to take this to next level run a program on a PC setup with an industrial vision camera and use the same prompting approach with strictiness levels and few-shot examples.

Most of the UI code is AI-generated and then manually debugged. The prompting logic and other setup was devloped through trial and error. Newer chatgpt models may be more effective in image analysis but I didn't get to test it. Hopefully in the next few years as OpenAI integrates their models with Microsoft something like this could be run just through Copilot.

## Overview
Watched folder: `/home/keyence/iv3_images`  
UI script: `watcher_ui.py` (Tkinter)  

## Setup
Use the setup_inspector.sh file to install the correct packages on your raspberry pi
