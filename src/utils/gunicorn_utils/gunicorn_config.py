# Gunicorn configuration file
import os
from pathlib import Path

bind = f":{os.getenv('PORT_NUMBER', '8766')}"

# IMPORTANT: keep workers=1 — the synthetic-data jobs and segmentation runs both use
# in-memory state that is not shared across processes.
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
max_requests = 100000
max_requests_jitter = 1000
timeout = 120
keepalive = 120
worker_tmp_dir = "/dev/shm"

loglevel = "info"
errorlog = str(Path(__file__).parent.parent.parent.parent / "logs/gunicorn_system.log")
access_log_format = '%(t)s [%(p)s] %(h)s "%(r)s" %(s)s'


def post_fork(server, worker):
    import logging
    from logging.handlers import RotatingFileHandler

    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers.clear()
    uvicorn_access.propagate = False
    handler = RotatingFileHandler(
        str(Path(__file__).parent.parent.parent.parent / "logs/uvicorn_access.log"),
        maxBytes=10485760, backupCount=5,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(process)d] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    uvicorn_access.addHandler(handler)
    uvicorn_access.setLevel(logging.INFO)
