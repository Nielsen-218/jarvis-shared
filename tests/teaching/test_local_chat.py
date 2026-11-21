import http.client
import http.server
import io
import json
import threading
from core import practice_agent


def test_chat_request_preserves_user_prompt_and_structured_result(monkeypatch):
    captured={}
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            captured['route']=self.path
            captured['body']=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            raw=json.dumps({'message':{'content':json.dumps({'answer':'unique-contract'})},'done_reason':'stop'}).encode()
            self.send_response(200);self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    original=http.client.HTTPConnection
    monkeypatch.setattr(practice_agent.http.client,'HTTPConnection',lambda *a,**k:original('127.0.0.1',server.server_port,timeout=5))
    class Installed:
        def open(self,*a,**k):return io.BytesIO(json.dumps({'models':[{'name':'local-test'}]}).encode())
    monkeypatch.setattr(practice_agent.urllib.request,'build_opener',lambda *a,**k:Installed())
    try:
        result,evidence=practice_agent.local_generate('unique-contract','local-test',chat=True)
        assert result=={'answer':'unique-contract'}
        assert captured['route']=='/api/chat' and 'prompt' not in captured['body']
        assert captured['body']['messages'][-1]=={'role':'user','content':'unique-contract'}
        assert evidence['api_route']=='/api/chat'
    finally:server.shutdown();server.server_close()
