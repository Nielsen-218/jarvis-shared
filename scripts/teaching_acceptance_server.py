"""Explicit isolated real AppServer + Brain. Never start production GUI/audio.

Uses a separate data directory and port, enforces loopback-only network and local
inference, and permits only read-only registry tools. Practice uses the new typed
owner endpoint and capability-limited language boundary. Exit via stdin 'stop'.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import threading


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--ready-file',type=Path,required=True)
    parser.add_argument('--token-file',type=Path,required=True)
    parser.add_argument('--public-research',action='store_true',help='Allow public research in this isolated data-only acceptance instance')
    args=parser.parse_args()
    os.environ.update({'JARVIS_LOCAL_ONLY':'1','JARVIS_INTELLIGENCE':'local-first',
        'JARVIS_LOCAL_FAST_PROVIDER':'ollama','JARVIS_LOCAL_FAST_MODEL':'llama3.1:8b',
        'JARVIS_OLLAMA_URL':'http://127.0.0.1:11434/v1','JARVIS_PUSH':'off',
        'JARVIS_VAULT':str(args.data_dir/'vault')})
    for key in list(os.environ):
        if key.startswith(('ANTHROPIC_','OPENAI_','AWS_','AZURE_')):
            os.environ.pop(key)
    original_connect=socket.socket.connect
    def guarded(self,address):
        host=address[0]
        try: permitted=ipaddress.ip_address(host).is_loopback
        except ValueError: permitted=host=='localhost'
        if args.public_research and not permitted:
            try:
                addresses=socket.getaddrinfo(host,address[1],type=socket.SOCK_STREAM)
                permitted=bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
            except OSError:permitted=False
        if not permitted: raise PermissionError('Isolated acceptance permits loopback only')
        return original_connect(self,address)
    socket.socket.connect=guarded
    from core import storage
    storage.DATA=args.data_dir.resolve()
    from core import appserver
    from core.config import Config, load_personality
    from brain.llm import Brain
    from tools import load_all, registry
    load_all()
    dispatch=registry.run
    def read_only(name,arguments,*a,**k):
        allowed={'library_search','library_list','jarvis_find_coding_procedure','jarvis_learning_status','jarvis_research_sources','jarvis_research_status'}
        if args.public_research:allowed|={'jarvis_learn_youtube','jarvis_web_search','jarvis_web_open','jarvis_web_follow','jarvis_web_mcp_page','jarvis_web_browse'}
        if name not in allowed:
            return 'Unavailable in read-only acceptance mode.',True
        return dispatch(name,arguments,*a,**k)
    registry.run=read_only
    cfg=Config()
    def make_brain(uid):
        return Brain(cfg.model,cfg.effort,load_personality(cfg,uid),cfg.conversation_timeout,uid=uid)
    instance=appserver.AppServer(make_brain)
    appserver.CACHE=storage.DATA/'app_cache'
    appserver.CACHE.mkdir(parents=True,exist_ok=True)
    if not args.token_file.exists():
        args.token_file.parent.mkdir(parents=True,exist_ok=True)
        args.token_file.write_text(appserver.add_device('owner','Isolated teaching acceptance'),encoding='utf-8')
    listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(128)
    import uvicorn
    server=uvicorn.Server(uvicorn.Config(instance.app,log_level='warning'))
    args.ready_file.write_text(json.dumps({'pid':os.getpid(),'port':listener.getsockname()[1],
        'real_app':'core.appserver.AppServer','real_brain':'brain.llm.Brain','local_only':True,
        'network_policy':('Public research and loopback' if args.public_research else 'Loopback-only')+' parent socket connect; candidate execution has a separate OS AppContainer boundary',
        'project_workspace_enabled':bool(os.environ.get('JARVIS_PROJECT_LAB_ROOT')),
        'source_sha256':{name:hashlib.sha256((Path(__file__).resolve().parents[1]/name).read_bytes()).hexdigest()
            for name in ('core/project_agent.py','core/project_tasks.py','core/project_sandbox.py','core/practice_agent.py')
            if (Path(__file__).resolve().parents[1]/name).exists()},
        'production_hub_modified':False}),encoding='utf-8')
    def control():
        import sys
        for line in sys.stdin:
            if line.strip()=='stop': server.should_exit=True; return
    threading.Thread(target=control,daemon=True).start()
    server.run(sockets=[listener])


if __name__=='__main__': main()
