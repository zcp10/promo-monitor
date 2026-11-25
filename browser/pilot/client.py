"""JSON over container loopback; no published host port."""
import json
import socket
import sys

request = json.loads(sys.stdin.buffer.read(4096))
with socket.create_connection(('127.0.0.1', 8765), timeout=3) as conn:
    conn.settimeout(60)
    conn.sendall(json.dumps(request).encode()+b'\n')
    with conn.makefile('rb') as stream:
        result = stream.readline(1048576)
        if not result:
            raise RuntimeError('worker disconnected')
        print(result.decode(), end='')
