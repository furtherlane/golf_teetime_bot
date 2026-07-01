#!/bin/bash
cd /Users/steve/apps/foreup-autores
exec caffeinate -dimsu .venv/bin/python selenium_reserve_tee_time.py >> launchd_run.log 2>&1
