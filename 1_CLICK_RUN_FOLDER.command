#!/bin/bash
# 1-Click macOS Launcher: Choose 1 folder and run entire clipping system
cd "/Users/hariomsingh/Desktop/mycliper"
source .venv/bin/activate
python3 run_folder.py "$@"
