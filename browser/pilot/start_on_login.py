"""Windows logon bootstrap: Docker Desktop -> isolated collector. No searches."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from logging.handlers import RotatingFileHandler

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DOCKER_DESKTOP = Path(r'C:\Program Files\Docker\Docker\Docker Desktop.exe')
DOCKER = Path(r'C:\Program Files\Docker\Docker\resources\bin\docker.exe')
POWERSHELL = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

def docker_ready(run=subprocess.run):
    try:
        result = run([str(DOCKER), '--config', str(HERE/'docker-config'),
            '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine',
            'info', '--format', '{{.ServerVersion}}'],
            capture_output=True, timeout=6, creationflags=HIDDEN)
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False

def launch_desktop():
    if not DOCKER_DESKTOP.is_file():
        raise FileNotFoundError('Docker Desktop não instalado no caminho esperado.')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    subprocess.Popen([str(DOCKER_DESKTOP)], startupinfo=startup,
        creationflags=HIDDEN, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def wait_for_docker(probe=docker_ready, sleep=time.sleep, attempts=48):
    for attempt in range(attempts):
        if probe():
            return True
        if attempt+1 < attempts:
            sleep(10)
    return False

def main():
    log_dir=ROOT/'logs'
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
        handlers=[RotatingFileHandler(log_dir/'coletor-startup.log', maxBytes=1048576, backupCount=2, encoding='utf-8')])
    logging.info('Inicialização automática do coletor iniciada.')
    if not docker_ready():
        logging.info('Abrindo Docker Desktop e aguardando serviço Linux.')
        launch_desktop()
        if not wait_for_docker():
            raise RuntimeError('Docker não ficou pronto; o Agendador tentará novamente.')
    result=subprocess.run([str(POWERSHELL), '-NoLogo', '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'RemoteSigned', '-WindowStyle', 'Hidden',
        '-File', str(HERE/'pilot.ps1'), '-Action', 'Start'],
        capture_output=True, timeout=180, creationflags=HIDDEN)
    if result.returncode:
        logging.error('Falha ao iniciar coletor: %s', result.stderr.decode(errors='replace')[-1500:])
        raise RuntimeError('Não foi possível iniciar o coletor.')
    # Health reads localhost inside the container; does not query Google or paid APIs.
    lines=result.stdout.decode('utf-8', errors='replace').splitlines()
    health=next((json.loads(line) for line in reversed(lines) if line.strip().startswith('{"status"')), {})
    if health.get('status') != 'ready':
        raise RuntimeError('Coletor iniciou sem confirmar estado pronto.')
    logging.info('Docker e coletor prontos. Histórico e limites preservados.')
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception('Inicialização automática falhou.')
        raise SystemExit(1)
