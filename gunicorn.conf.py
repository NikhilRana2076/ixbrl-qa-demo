# One worker process: per-visitor fact stores and quota counters live in this
# process's memory, so they must not be split across workers. Threads give
# concurrency while a request waits on the model API.
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1
threads = 4
timeout = 120            # parsing a 25 MB filing + two model calls
graceful_timeout = 20
keepalive = 5
accesslog = "-"
# Do not log query strings or bodies: questions stay out of the logs.
access_log_format = '%(h)s "%(m)s %(U)s" %(s)s %(b)s %(L)ss'
