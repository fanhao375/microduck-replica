"""Explicit localhost-only browser fixture using the real Controller and package staging.

No production entry point imports this module. Engine/preflight are test doubles;
the separate QEMU integration tests exercise the real bundled transaction engine.
"""
import argparse
import json
from pathlib import Path
import socketserver
import sys
import tempfile
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from control import Controller,UpdateError,fetch
from package import MAX_OUTER

class FixtureEngine:
    def __init__(self): self.version='2026.10.26'; self.previous='2026.10.25'
    def call(self,method,params=None,progress=None):
        if method=='update.status':return [{'component':'software','installed':self.version,'phase':'idle'}]
        if method=='update.listInstalled':return [{'version':self.version,'active':True},{'version':self.previous,'active':False}]
        if progress:
            progress({'component':'software','phase':'verifying','percent':None});time.sleep(.3)
        if method=='update.rollback':self.version,self.previous=self.previous,self.version;return {'outcome':'applied','to':self.version}
        version=params['target']['exact']
        if params.get('options',{}).get('dry_run'):return {'outcome':'dry_run_passed','candidate':version}
        if progress:
            progress({'component':'software','phase':'health_gate','percent':None});time.sleep(2)
        self.previous,self.version=self.version,version
        return {'outcome':'applied','to':version}

class Fixture:
    def __init__(self,root,package=None,marker=None):
        self.root=Path(root);self.package=Path(package) if package else None;self.waiting=False;self.no_release=True
        self.engine=FixtureEngine()
        self.controller=Controller(self.root,self.engine,self.preflight,self.catalog,marker=marker,fetcher=self.download)
    def preflight(self):
        if self.waiting:raise UpdateError('preflight','测试鸭子的运动模型正在接管')
    def catalog(self):
        if self.no_release:return []
        if not self.package:raise UpdateError('network','本地发布源连接失败')
        return [{'tag_name':'microduck-software-v2026.10.27','draft':False,'prerelease':False,'body':'本地语音坐下/站起与网页更新测试',
            'assets':[{'name':'2026.10.27.software-update.tar','size':self.package.stat().st_size,
            'browser_download_url':'https://github.com/fanhao375/microduck-replica/releases/download/microduck-software-v2026.10.27/2026.10.27.software-update.tar'}]}]
    def download(self,url,stream,maximum):
        return fetch(f'http://127.0.0.1:{self.provider.server_port}/package',stream,maximum,loopback=True)
    def serve(self,port=0):
        fixture=self
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Provider(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path!='/package' or not fixture.package:
                    self.send_error(404);return
                self.send_response(200);self.send_header('Content-Length',str(fixture.package.stat().st_size));self.end_headers()
                with fixture.package.open('rb') as source:
                    import shutil
                    shutil.copyfileobj(source,self.wfile)
            def log_message(self,*args):pass
        self.provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        threading.Thread(target=self.provider.serve_forever,daemon=True).start()
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    self.connection.settimeout(10);request=json.loads(self.rfile.readline(8193));op=request.get('op')
                    if op=='fixture':
                        if 'waiting' in request:fixture.waiting=request['waiting']
                        if 'no_release' in request:fixture.no_release=request['no_release']
                        result={'ok':True}
                    elif op=='status':fixture.controller.refresh();result={'ok':True,**fixture.controller.view()}
                    elif op=='upload':
                        size=request['size']
                        if not 0<size<=MAX_OUTER:raise UpdateError('oversize','file too large')
                        with tempfile.TemporaryFile() as stream:
                            remaining=size
                            while remaining:
                                chunk=self.rfile.read(min(1024*1024,remaining))
                                if not chunk:raise UpdateError('upload','incomplete upload')
                                stream.write(chunk);remaining-=len(chunk)
                            stream.seek(0);result={'ok':True,**fixture.controller.upload(stream)}
                    else:result={'ok':True,**fixture.controller.start(op)}
                except Exception as error:result={'ok':False,'code':getattr(error,'code','service'),'error':str(error)}
                self.wfile.write(json.dumps(result,ensure_ascii=False).encode()+b'\n')
        server=socketserver.ThreadingTCPServer(('127.0.0.1',port),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        original_shutdown=server.shutdown
        def shutdown(): original_shutdown();fixture.provider.shutdown()
        server.shutdown=shutdown
        return server

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--package');parser.add_argument('--port',type=int,default=18091)
    args=parser.parse_args();fixture=Fixture(args.root,args.package);server=fixture.serve(args.port)
    print(json.dumps({'address':f'tcp:127.0.0.1:{server.server_address[1]}'}),flush=True)
    try:threading.Event().wait()
    finally:server.shutdown()
