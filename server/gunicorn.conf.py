"""Production WSGI defaults; per-service capacity stays in systemd."""

import os

# systemd owns lifecycle management; do not create a second control endpoint.
control_socket_disable = True
# Keep worker heartbeat I/O off the SD/SSD. macOS development uses its default.
worker_tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None
worker_class = "sync"
