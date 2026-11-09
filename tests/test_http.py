import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import data
import server

class HTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.db=Path(cls.temp.name)/'http.db'
        data.initialize(cls.db,False)
        cls.server=server.AppServer(('127.0.0.1',0),cls.db)
        cls.port=cls.server.server_address[1]
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,body=None,cookie=None,csrf=None,origin=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        headers={}
        if cookie: headers['Cookie']=cookie
        if csrf: headers['X-CSRF-Token']=csrf
        if origin: headers['Origin']=origin
        if body is not None: headers['Content-Type']='application/json'
        conn.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
        r=conn.getresponse();raw=r.read();status=r.status;cookie=r.getheader('Set-Cookie');mime=r.getheader('Content-Type');conn.close()
        return status,json.loads(raw) if mime.startswith('application/json') else raw,cookie
    def test_complete_auth_permission_and_ai_contract(self):
        self.assertEqual(self.request('/api/analytics')[0],401)
        self.assertEqual(self.request('/api/auth/status')[1]['setup_required'],True)
        self.assertEqual(self.request('/api/auth/setup',{'username':'admin','password':'short'})[0],400)
        status,result,cookie=self.request('/api/auth/setup',{'username':'qa','password':'test-only-password'})
        self.assertEqual(status,200);csrf=result['user']['csrf']
        self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        self.assertEqual(self.request('/api/auth/setup',{'username':'x','password':'test-only-password'})[0],409)
        self.assertEqual(self.request('/api/views',{'name':'x','filters':{}},cookie)[0],403)
        self.assertEqual(self.request('/api/views',{'name':'x','filters':{}},cookie,csrf,'https://evil.example')[0],403)
        self.assertEqual(self.request('/api/analytics',cookie=cookie)[0],200)
        self.assertEqual(self.request('/api/views',{'name':'我的看板','filters':{'start':'2026-10-01','end':'2026-10-07'}},cookie,csrf)[0],201)
        self.assertEqual(len(self.request('/api/views',cookie=cookie)[1]),1)
        self.assertEqual(self.request('/api/ai/chat',{'message':'test'},cookie,csrf)[0],501)
        self.assertEqual(self.request('/api/ai/status',cookie=cookie)[1]['enabled'],False)
        self.assertEqual(self.request('/api/admin/accounts',{'username':'ops','password':'test-only-password','role':'operations','channel':'all'},cookie,csrf)[0],201)
        _,ops,ops_cookie=self.request('/api/auth/login',{'username':'ops','password':'test-only-password'})
        self.assertEqual(self.request('/api/admin',cookie=ops_cookie)[0],403)
        self.assertEqual(self.request('/api/sql',{'sql':'SELECT 1'},ops_cookie,ops['user']['csrf'])[0],403)
        self.assertEqual(self.request('/api/segments',cookie=ops_cookie)[0],403)
        self.assertEqual(self.request('/api/views',cookie=ops_cookie)[1],[])
        self.assertEqual(self.request('/api/auth/logout',{},cookie,csrf)[0],200)
        self.assertEqual(self.request('/api/meta',cookie=cookie)[0],401)
    def test_static_boundaries(self):
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/../server.py')[0],404)
        self.assertEqual(self.request('/data/insight.db')[0],404)

if __name__=='__main__': unittest.main()
