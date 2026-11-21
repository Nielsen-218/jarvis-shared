"""Ephemeral browser egress proxy: pinned public addresses, finite encrypted bytes.

Only the explicitly supplied synthetic fixture origin may bypass public HTTPS.
Production tools never accept this fixture parameter from a model or HTTP body.
"""
import http.client
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import select
import socket
import secrets
import threading
from urllib.parse import urlsplit


class Egress:
    def __init__(self,fixture_origin=None,allowed_origin=None,*,max_requests=24,max_bytes=16*1024*1024):
        self.max_requests=max_requests;self.max_bytes=max_bytes
        self.fixture=fixture_origin
        self.allowed_origin=allowed_origin
        self.username='jarvis-bounded'
        self.password=secrets.token_urlsafe(24)
        self.authorization='Basic '+base64.b64encode((self.username+':'+self.password).encode()).decode()
        self.lock=threading.Lock();self.bytes=0;self.requests=0;self.blocked=0

    def address(self,url):
        p=urlsplit(url)
        allowed=urlsplit(self.allowed_origin) if self.allowed_origin else None
        if allowed and (p.scheme,p.hostname,p.port or 443)!=(allowed.scheme,allowed.hostname,allowed.port or 443):
            raise ValueError('Cross-site account egress rejected')
        fixture=bool(self.fixture and (p.scheme+'://'+p.netloc)==self.fixture)
        if p.username is not None or p.password is not None or '\\' in url:
            raise ValueError('Credentials in browser URL rejected')
        if not fixture and (p.scheme!='https' or p.port not in (None,443)):
            raise ValueError('Public HTTPS required')
        addresses=socket.getaddrinfo(p.hostname,p.port or 443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses) and not fixture:
            raise ValueError('Private browser destination rejected')
        with self.lock:
            self.requests+=1
            if self.requests>self.max_requests:raise ValueError('Browser request quota')
        return addresses[0][4][0],p.port or 443

    def account(self,count):
        with self.lock:
            self.bytes+=count
            if self.bytes>self.max_bytes:raise ValueError('Browser transfer quota')


def start(policy):
    class Handler(BaseHTTPRequestHandler):
        timeout=10
        def log_message(self,*args):pass

        def authenticated(self):
            if hmac.compare_digest(self.headers.get('Proxy-Authorization',''),policy.authorization):return True
            self.send_response(407);self.send_header('Proxy-Authenticate','Basic realm="Jarvis bounded egress"')
            self.send_header('Content-Length','0');self.end_headers();self.close_connection=True
            return False

        def do_CONNECT(self):
            if not self.authenticated():return
            upstream=None
            try:
                address=policy.address('https://'+self.path)
                upstream=socket.create_connection(address,timeout=8)
                self.send_response(200);self.end_headers()
                self.connection.settimeout(10);upstream.settimeout(10)
                while True:
                    readable,_,_=select.select([self.connection,upstream],[],[],10)
                    if not readable:return
                    for source in readable:
                        chunk=source.recv(16384)
                        if not chunk:return
                        policy.account(len(chunk))
                        (upstream if source is self.connection else self.connection).sendall(chunk)
            except (OSError,ValueError):
                with policy.lock:policy.blocked+=1
                if upstream is None:
                    try:self.send_error(403)
                    except OSError:pass
            finally:
                if upstream:upstream.close()
                self.close_connection=True

        def fixture_request(self):
            if not self.authenticated():return
            try:
                p=urlsplit(self.path)
                if not policy.fixture or p.scheme+'://'+p.netloc!=policy.fixture:raise ValueError('HTTP denied')
                address=policy.address(self.path)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<=length<=8192:raise ValueError('Body quota')
                body=self.rfile.read(length)
                conn=http.client.HTTPConnection(address[0],address[1],timeout=5)
                try:
                    headers={k:v for k,v in self.headers.items() if k.lower() not in {'proxy-connection','proxy-authorization','connection','host'}}
                    conn.request(self.command,(p.path or '/')+('?' +p.query if p.query else ''),body,headers)
                    r=conn.getresponse();data=r.read(2*1024*1024+1)
                    if len(data)>2*1024*1024:raise ValueError('Fixture response quota')
                    policy.account(len(data));self.send_response(r.status)
                    for k,v in r.getheaders():
                        if k.lower() not in {'transfer-encoding','connection','content-length'}:self.send_header(k,v)
                    self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                finally:conn.close()
            except (OSError,ValueError):
                with policy.lock:policy.blocked+=1
                self.send_error(403)

        do_GET=fixture_request
        do_POST=fixture_request
        do_HEAD=fixture_request
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server
