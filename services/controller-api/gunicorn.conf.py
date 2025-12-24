"""Gunicorn configuration for Flask controller API."""

import multiprocessing
import os

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', 8080)}"
backlog = 2048

# Worker processes
workers = os.getenv("WORKERS", multiprocessing.cpu_count() * 2 + 1)
worker_class = "sync"
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")
access_log_format = (
    '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'
)

# Server mechanics
daemon = False
pidfile = None
temp_upload_dir = None

# Server hooks
preload_app = True
forwarded_allow_ips = "*"

# Process naming
proc_name = "penguinwhisk-controller"

# Environment
raw_env = [
    "PYTHONUNBUFFERED=1",
]
