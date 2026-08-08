"""Weather Agent 的 Gunicorn 生产运行配置。"""

bind = "127.0.0.1:8000"
workers = 1
worker_class = "gthread"
threads = 4

timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"
capture_output = True
