"""Proxy de saída limitado a destinos públicos da comparação; sem acesso ao host."""
import asyncio
import ipaddress
import json
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit

DOMAINS=(
 'google.com','google.com.br','gstatic.com','googleusercontent.com','googleapis.com','ggpht.com',
)

def log(**data):
    print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),**data}),flush=True)

def allowed(host):
    return any(host==d or host.endswith('.'+d) for d in DOMAINS)

async def relay(reader,writer):
    try:
        while data:=await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError,asyncio.CancelledError):
        pass
    finally:
        writer.close()

async def handle(reader,writer):
    remote=None
    try:
        header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),15)
        if len(header)>16384:raise ValueError('header size')
        method,target,version=header.split(b'\r\n',1)[0].decode('ascii').split(' ',2)
        if method=='CONNECT':
            host,port=target.rsplit(':',1);port=int(port)
        else:
            url=urlsplit(target)
            host,port=url.hostname,url.port or 80
        host=(host or '').lower().rstrip('.')
        if not allowed(host) or port not in (80,443):
            log(action='DENY',host=host,port=port,reason='allowlist')
            writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n');await writer.drain();return
        addresses=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM)
        public=[a for a in addresses if ipaddress.ip_address(a[4][0]).is_global]
        if not public or len(public)!=len(addresses):
            log(action='DENY',host=host,reason='non-public address');return
        address=public[0][4][0]
        upstream,remote=await asyncio.wait_for(asyncio.open_connection(address,port),20)
        log(action='ALLOW',host=host,port=port,address=address)
        if method=='CONNECT':
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n');await writer.drain()
        else:
            parsed=urlsplit(target)
            origin=parsed.path or '/'
            if parsed.query:origin+='?'+parsed.query
            request=f'{method} {origin} {version}\r\n'.encode()+header.split(b'\r\n',1)[1]
            remote.write(request);await remote.drain()
        await asyncio.gather(relay(reader,remote),relay(upstream,writer))
    except Exception as e:
        log(action='ERROR',type=type(e).__name__)
    finally:
        writer.close()
        if remote:remote.close()

async def main():
    server=await asyncio.start_server(handle,'0.0.0.0',8080,limit=20000)
    log(action='READY')
    async with server:await server.serve_forever()

asyncio.run(main())
