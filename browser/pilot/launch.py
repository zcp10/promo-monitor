import os
from pathlib import Path
import secrets
import subprocess
import time

Path('/tmp/home').mkdir(exist_ok=True)
# This named profile belongs exclusively to this single container. Remove stale
# Chromium symlinks from a prior container hostname after it has stopped.
for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
    link = Path('/state/profile') / name
    if link.is_symlink():
        link.unlink()
auth = Path('/tmp/Xauthority')
auth.touch(mode=0o600)
os.environ.update(DISPLAY=':99', XAUTHORITY=str(auth))
subprocess.run(['xauth', '-f', str(auth), 'add', ':99', 'MIT-MAGIC-COOKIE-1', secrets.token_hex(16)], check=True)
display = subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1280x900x24', '-nolisten', 'tcp', '-auth', str(auth)])
try:
    for _ in range(100):
        if Path('/tmp/.X11-unix/X99').exists():
            break
        if display.poll() is not None:
            raise RuntimeError('Xvfb failed')
        time.sleep(.1)
    subprocess.run(['python', '/pilot/worker.py'], check=True)
finally:
    display.terminate()
