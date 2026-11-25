"""Host-side transport only. Browser/dependencies/separate profile stay in Docker."""
import asyncio
import json
import subprocess
from .config import ROOT

async def search(config, query):
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            config.get('docker_executable', 'docker'), '--config', str(ROOT / 'browser' / 'pilot' / 'docker-config'),
            '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine',
            'exec', '-i', 'radar-google-pilot', 'python', '/pilot/client.py',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        payload = {'query': query, 'interval_seconds': config.get('interval_seconds', 60),
                   'hourly_limit': config.get('hourly_limit', 10)}
        stdout, _ = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()), timeout=65)
        if proc.returncode:
            return {'status': 'docker_unavailable', 'quotes': []}
        return json.loads(stdout)
    except (OSError, ValueError, asyncio.TimeoutError):
        return {'status': 'docker_unavailable', 'quotes': []}
    finally:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
