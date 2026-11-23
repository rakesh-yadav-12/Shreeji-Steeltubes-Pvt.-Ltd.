# Run:  gunicorn -c gunicorn.conf.py app:app
# On Render: Start Command =  gunicorn -c gunicorn.conf.py app:app   (Render sets $PORT itself)
import multiprocessing, os
bind = "0.0.0.0:" + os.getenv("PORT", "8000")             # Render (and most hosts) need 0.0.0.0 + their $PORT, not 127.0.0.1
# Render free/starter plans have very little RAM (512MB-ish) and often report the HOST's full CPU count,
# which made this spawn way too many workers (e.g. 2*16+1=33) and starve the app for memory -> everything felt slow.
# Default is now a small, safe number. Override with the WEB_WORKERS env var if you upgrade your Render plan.
workers = int(os.getenv("WEB_WORKERS", "2"))
threads = int(os.getenv("WEB_THREADS", 4))
worker_class = "gthread"                                  # threads = good for Mongo / network waits
worker_connections = 1000
backlog = 4096
keepalive = 30
timeout = 60
graceful_timeout = 30
max_requests = 20000                                      # recycle workers to avoid memory creep
max_requests_jitter = 2000
preload_app = True                                        # load code once, share memory between workers
accesslog = None                                          # logging every hit costs speed
errorlog = "-"

def post_fork(server, worker):
    """preload_app=True means app.py (and its MongoClient, created in db.init() at import
    time) is loaded ONCE in the master process and then each worker is forked from it.
    A MongoClient's background connection-monitoring threads do not survive fork(), so every
    worker would otherwise start life sharing a Mongo connection object created by a
    different process - this is a well-known source of random, hard-to-explain "server
    error" responses on whichever request happens to be the first real database call a
    worker makes (very often the /auth "enter your details" page, since that is the first
    page a brand-new visitor's session touches). Dropping the cached handle here forces each
    worker to open its own fresh MongoClient the first time it actually needs the database."""
    import db
    db._db = None
