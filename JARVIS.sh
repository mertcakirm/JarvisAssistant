#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
nohup python3 main.py > /dev/null 2>&1 &
