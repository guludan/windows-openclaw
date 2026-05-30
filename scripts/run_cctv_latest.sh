#!/bin/bash
cd /home/tearsyu/.openclaw/workspace
exec ~/miniconda3/envs/tt/bin/python3 scripts/data_acquisition.py --cctv --latest
