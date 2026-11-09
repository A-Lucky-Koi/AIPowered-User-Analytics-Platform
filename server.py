"""Game Insight local server. Python 3.10+, standard library only."""
import argparse
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import data

ROLE_NAMES={'admin':'管理员','analyst':'数据分析师','operations':'运营','viewer':'管理层'}
ROLE_ACCESS={
    'admin':{'summary','sql','segments','admin','payment'},
    'analyst':{'summary','sql','segments','payment'},
    'operations':{'summary'},
    'viewer':{'summary','payment'},
}

class APIError(Exception):
    def __init__(self,status,message): self.status=status; self.message=message

def password_hash(password,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),260000).hex()

def check_password(password,stored):
    return hmac.compare_digest(password_hash(password,stored.split(':')[0]),stored)

def require(account,permission):
    if permission not in ROLE_ACCESS[account['role']]: raise APIError(403,'当前角色没有此功能或敏感数据的访问权限')

def audit(c,account,action,detail):
    c.execute('INSERT INTO audit(account_id,action,detail,created) VALUES (?,?,?,?)',[account.get('id'),action,json.dumps(detail,ensure_ascii=False),data.now()])

def safe_sql(c,sql,filters,account):
    """Run only SELECT against an isolated, scoped in-memory analytics snapshot."""
    require(account,'sql')
    if not isinstance(sql,str) or len(sql)>8000 or not sql.lstrip().upper().startswith('SELECT '):
        raise ValueError('仅支持以 SELECT 开始的单条只读查询（最多 8,000 字符）')
    result=data.analytics(c,filters,account)
    sandbox=sqlite3.connect(':memory:')
    sandbox.row_factory=sqlite3.Row
    try:
        sandbox.executescript('''CREATE TABLE analytics_daily(date TEXT,dau INTEGER,new INTEGER,"returning" INTEGER,revenue REAL,payers INTEGER,pay_rate REAL,arpu REAL,avg_minutes REAL);
            CREATE TABLE analytics_channels(channel TEXT,active INTEGER,new INTEGER,revenue REAL,spend REAL,roas REAL,d1 REAL,d7 REAL);
            CREATE TABLE analytics_cohorts(date TEXT,size INTEGER,d1 REAL,d2 REAL,d3 REAL,d7 REAL,d14 REAL,d30 REAL);''')
        sandbox.executemany('INSERT INTO analytics_daily VALUES (?,?,?,?,?,?,?,?,?)',[[r[k] for k in ['date','dau','new','returning','revenue','payers','pay_rate','arpu','avg_minutes']] for r in result['trend']])
        sandbox.executemany('INSERT INTO analytics_channels VALUES (?,?,?,?,?,?,?,?)',[[r[k] for k in ['name','active','new','revenue','spend','roas','d1','d7']] for r in result['channels']])
        sandbox.executemany('INSERT INTO analytics_cohorts VALUES (?,?,?,?,?,?,?,?)',[[r['date'],r['size'],*r['values']] for r in result['retention']['rows']])
        safe_functions={'count','sum','avg','min','max','round','coalesce','nullif','abs','lower','upper','length','substr','date','strftime','julianday','ifnull'}
        tables={'analytics_daily','analytics_channels','analytics_cohorts'}
        def authorize(action,a,b,db,source):
            if action==sqlite3.SQLITE_SELECT: return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_READ and a in tables: return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_FUNCTION and b.lower() in safe_functions: return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        sandbox.set_authorizer(authorize)
        deadline=time.monotonic()+3
        steps=0
        def progress():
            nonlocal steps
            steps+=1
            return int(time.monotonic()>deadline or steps>5000)
        sandbox.set_progress_handler(progress,1000)
        started=time.monotonic()
        try:
            cursor=sandbox.execute(sql.strip().rstrip(';'))
        except sqlite3.Warning as exc:
            raise ValueError('仅允许单条 SELECT 查询') from exc
        rows=cursor.fetchmany(1001)
        return {'columns':[d[0] for d in cursor.description],'rows':[list(r) for r in rows[:1000]],'truncated':len(rows)>1000,'elapsed_ms':round((time.monotonic()-started)*1000,1)}
    finally:
        sandbox.close()

class AppServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,db_path):
        super().__init__(address,Handler)
        self.db_path=db_path
        self.auth_lock=threading.Lock()
        self.login_failures={}

class Handler(BaseHTTPRequestHandler):
    server_version='GameInsight/1.0'
    protocol_version='HTTP/1.1'

    def log_message(self,format,*args):
        # Do not log query strings, credentials or user CSV data.
        print(f'{self.client_address[0]} {self.command} {urlparse(self.path).path}',flush=True)

    def send(self,status,body,content_type='application/json; charset=utf-8',headers=None):
        if status>=400:
            self.close_connection=True
        if not isinstance(body,bytes): body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        if self.close_connection: self.send_header('Connection','close')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Cache-Control','no-store' if self.path.startswith('/api') else 'no-cache')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if not 0<n<=12_000_000: raise APIError(413,'请求内容为空或超过 12 MB')
        result=json.loads(self.rfile.read(n))
        if not isinstance(result,dict): raise ValueError('请求体必须为 JSON 对象')
        return result

    def account(self,c):
        cookies=SimpleCookie()
        try: cookies.load(self.headers.get('Cookie',''))
        except Exception: raise APIError(401,'请先登录')
        token=cookies.get('gi_session')
        row=c.execute('SELECT a.id,a.username,a.role,a.channel,s.csrf FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token=? AND s.expires>?',[hashlib.sha256(token.value.encode()).hexdigest() if token else '',time.time()]).fetchone()
        if not row: raise APIError(401,'登录已过期，请重新登录')
        return dict(row)

    def filter(self,c,account,raw=None):
        raw=raw or {k:v[-1] for k,v in parse_qs(urlparse(self.path).query).items()}
        return data.parse_filters(raw,data.metadata(c,account))

    def do_GET(self): self.handle_request(False)
    def do_POST(self): self.handle_request(True)

    def handle_request(self,post):
        try:
            host=urlparse('http://'+self.headers.get('Host','')).hostname
            if self.server.server_address[0] in ('127.0.0.1','::1') and host not in ('localhost','127.0.0.1','::1'):
                raise APIError(403,'不允许的本地服务 Host')
            path=urlparse(self.path).path
            if not path.startswith('/api/'):
                if post: raise APIError(405,'不支持此方法')
                return self.static(path)
            with data.connect(self.server.db_path) as c:
                if path=='/api/health': return self.send(200,{'status':'ok','version':'1.0','ai_enabled':False})
                if path=='/api/auth/status' and not post:
                    setup=not c.execute('SELECT 1 FROM accounts LIMIT 1').fetchone()
                    try: user=self.account(c)
                    except APIError: user=None
                    return self.send(200,{'setup_required':setup,'user':user})
                if post:
                    origin=self.headers.get('Origin')
                    if origin and urlparse(origin).netloc != self.headers.get('Host'):
                        raise APIError(403,'拒绝跨站请求')
                    if self.headers.get('Sec-Fetch-Site')=='cross-site': raise APIError(403,'拒绝跨站请求')
                if path in ('/api/auth/setup','/api/auth/login') and post: return self.login(c,path)
                user=self.account(c)
                if post and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),user['csrf']):
                    raise APIError(403,'请求验证失败，请刷新后重试')
                c.execute('BEGIN')
                if post: self.post_api(c,path,user)
                else: self.get_api(c,path,user)
        except APIError as exc: self.send(exc.status,{'error':exc.message})
        except (ValueError,KeyError,TypeError,OverflowError) as exc: self.send(400,{'error':str(exc)})
        except sqlite3.Error as exc: self.send(400,{'error':'数据库拒绝请求：'+str(exc)})
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError): pass
        except Exception:
            import traceback
            traceback.print_exc()
            self.send(500,{'error':'服务内部错误，请查看本地服务日志'})

    def login(self,c,path):
        payload=self.body()
        username=str(payload.get('username','')).strip()
        password=str(payload.get('password',''))
        if not 1<=len(username)<=40 or len(password)>256: raise ValueError('用户名或密码长度不正确')
        ip=self.client_address[0]
        with self.server.auth_lock:
            attempts=[t for t in self.server.login_failures.get(ip,[]) if t>time.time()-300]
            if len(attempts)>=10: raise APIError(429,'尝试过于频繁，请 5 分钟后重试')
            if path.endswith('setup'):
                c.execute('BEGIN IMMEDIATE')
                if c.execute('SELECT 1 FROM accounts LIMIT 1').fetchone(): raise APIError(409,'管理员已创建，请登录')
                if len(password)<10: raise ValueError('密码至少需要 10 个字符')
                c.execute('INSERT INTO accounts(username,password,role,created) VALUES (?,?,?,?)',[username,password_hash(password),'admin',data.now()])
            row=c.execute('SELECT * FROM accounts WHERE username=?',[username]).fetchone()
            if not row or not check_password(password,row['password']):
                self.server.login_failures[ip]=attempts+[time.time()]
                raise APIError(401,'用户名或密码不正确')
            self.server.login_failures.pop(ip,None)
        token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(24)
        c.execute('DELETE FROM sessions WHERE expires<?',[time.time()])
        c.execute('INSERT INTO sessions VALUES (?,?,?,?)',[hashlib.sha256(token.encode()).hexdigest(),row['id'],csrf,time.time()+8*3600])
        audit(c,dict(row),'login',{'username':username})
        c.commit()
        self.send(200,{'user':{'id':row['id'],'username':username,'role':row['role'],'channel':row['channel'],'csrf':csrf}},headers={'Set-Cookie':f'gi_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'})

    def get_api(self,c,path,user):
        if path=='/api/meta': return self.send(200,data.metadata(c,user))
        if path=='/api/analytics':
            require(user,'summary'); return self.send(200,data.analytics(c,self.filter(c,user),user))
        if path=='/api/views':
            rows=c.execute('SELECT * FROM saved_views WHERE account_id=? ORDER BY id DESC',[user['id']]).fetchall()
            return self.send(200,[{**dict(r),'filters':json.loads(r['filters'])} for r in rows])
        if path=='/api/segments':
            require(user,'segments')
            rows=c.execute('SELECT * FROM segments WHERE account_id=? ORDER BY id DESC',[user['id']]).fetchall()
            return self.send(200,[{**dict(r),'rules':json.loads(r['rules'])} for r in rows])
        if path=='/api/admin':
            require(user,'admin')
            return self.send(200,{'accounts':[dict(r) for r in c.execute('SELECT id,username,role,channel,created FROM accounts')],'rules':[dict(r) for r in c.execute('SELECT * FROM alert_rules')],'audit':[dict(r) for r in c.execute('SELECT id,account_id,action,detail,created FROM audit ORDER BY id DESC LIMIT 100')],'counts':{t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ['players','activity','spend']}})
        if path=='/api/export':
            result=data.analytics(c,self.filter(c,user),user)
            audit(c,user,'export',result['filters']); c.commit()
            rows=result['trend']
            fields=['date','dau','new','returning']+(['revenue','pay_rate','arpu'] if user['role']!='operations' else [])
            return self.send_csv(rows,fields,'game-insight.csv')
        if path=='/api/import-template':
            require(user,'admin')
            row=dict(zip(data.IMPORT_FIELDS,['device-001','2026-11-01','自然流量','Android','星河一区','V2.1','2026-11-01',45,2,0,5,4,20]))
            return self.send_csv([row],data.IMPORT_FIELDS,'activity-template.csv')
        if path=='/api/ai/status': return self.send(200,{'enabled':False,'provider':None,'message':'AI 尚未接入，未发送任何数据。接口契约见 docs/AI_GATEWAY.md。'})
        raise APIError(404,'接口不存在')

    def post_api(self,c,path,user):
        payload=self.body()
        if path=='/api/auth/logout':
            c.execute('DELETE FROM sessions WHERE account_id=? AND csrf=?',[user['id'],user['csrf']]); c.commit()
            return self.send(200,{'ok':True},headers={'Set-Cookie':'gi_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
        if path=='/api/views':
            name=str(payload.get('name','')).strip()
            if not 1<=len(name)<=60: raise ValueError('看板名称需为 1–60 字符')
            filters=data.parse_filters(payload['filters'],data.metadata(c,user))
            filters['page']=payload['filters'].get('page','overview')
            c.execute('INSERT INTO saved_views(account_id,name,filters,created) VALUES (?,?,?,?)',[user['id'],name,json.dumps(filters,ensure_ascii=False),data.now()])
            c.commit(); return self.send(201,{'ok':True})
        if path=='/api/views/delete':
            c.execute('DELETE FROM saved_views WHERE id=? AND account_id=?',[int(payload['id']),user['id']]); c.commit(); return self.send(200,{'ok':True})
        if path=='/api/segments/preview' or path=='/api/segments/export' or path=='/api/segments':
            require(user,'segments')
            rules=payload.get('rules',{})
            rows=data.segment_members(c,rules,user)
            if path.endswith('/preview'): return self.send(200,{'count':len(rows),'rows':rows[:100],'by_channel':[{ 'name':k,'value':sum(r['channel']==k for r in rows)} for k in sorted(set(r['channel'] for r in rows))]})
            if path.endswith('/export'):
                audit(c,user,'segment_export',{'rules':rules,'count':len(rows)}); c.commit()
                return self.send_csv(rows,['user','channel','os','server','level','last_active','ltv','avg_minutes'],'segment.csv')
            name=str(payload.get('name','')).strip()
            if not 1<=len(name)<=60: raise ValueError('分群名称需为 1–60 字符')
            c.execute('INSERT INTO segments(account_id,name,rules,created) VALUES (?,?,?,?)',[user['id'],name,json.dumps(rules,ensure_ascii=False),data.now()])
            audit(c,user,'segment_save',{'name':name,'rules':rules}); c.commit(); return self.send(201,{'ok':True,'count':len(rows)})
        if path=='/api/segments/delete':
            require(user,'segments'); c.execute('DELETE FROM segments WHERE id=? AND account_id=?',[int(payload['id']),user['id']]); c.commit(); return self.send(200,{'ok':True})
        if path=='/api/sql':
            require(user,'sql')
            try:
                result=safe_sql(c,payload.get('sql'),self.filter(c,user,payload.get('filters')),user)
            except (ValueError,sqlite3.Error) as exc:
                audit(c,user,'sql_rejected',{'sql':str(payload.get('sql'))[:8000],'error':str(exc)}); c.commit(); raise
            audit(c,user,'sql',{'sql':payload['sql'],'rows':len(result['rows']),'filters':payload.get('filters'),'elapsed_ms':result['elapsed_ms']}); c.commit()
            return self.send(200,result)
        if path=='/api/alerts/ack':
            f=self.filter(c,user,payload.get('filters'))
            current=data.analytics(c,f,user)['alerts']
            if payload.get('key') not in [a['key'] for a in current]: raise ValueError('告警不在当前权限与筛选范围内')
            c.execute('INSERT OR REPLACE INTO alert_ack VALUES (?,?,?)',[payload['key'],user['id'],data.now()]); audit(c,user,'alert_ack',{'key':payload['key']}); c.commit()
            return self.send(200,{'ok':True})
        if path=='/api/admin/rules':
            require(user,'admin')
            metric=payload['metric']; threshold=float(payload['threshold'])
            if metric not in ('dau','new','revenue') or not 1<=threshold<=1000: raise ValueError('阈值需为 1–1000%')
            c.execute('UPDATE alert_rules SET threshold=?,enabled=? WHERE metric=?',[threshold,int(bool(payload.get('enabled',True))),metric]); audit(c,user,'alert_rule',payload); c.commit(); return self.send(200,{'ok':True})
        if path=='/api/admin/accounts':
            require(user,'admin')
            username=str(payload.get('username','')).strip(); password=str(payload.get('password','')); role=payload.get('role'); channel=payload.get('channel','all')
            if not 1<=len(username)<=40 or not 10<=len(password)<=256 or role not in ROLE_NAMES: raise ValueError('用户名需 1–40 字符，密码需 10–256 字符，角色需有效')
            if channel!='all' and channel not in data.metadata(c,user)['dimensions']['channel']: raise ValueError('渠道不存在')
            if role=='admin' and channel!='all': raise ValueError('管理员必须具有全渠道范围')
            c.execute('INSERT INTO accounts(username,password,role,channel,created) VALUES (?,?,?,?,?)',[username,password_hash(password),role,channel,data.now()]); audit(c,user,'account_create',{'username':username,'role':role,'channel':channel}); c.commit(); return self.send(201,{'ok':True})
        if path=='/api/admin/import':
            require(user,'admin')
            if payload.get('confirm') is not True: raise ValueError('请先确认导入方式；首次导入会替换演示数据')
            result=data.import_csv(c,payload['csv']); audit(c,user,'import',result); c.commit(); return self.send(200,result)
        if path=='/api/admin/spend':
            require(user,'admin')
            date=payload['date']; data.dt.date.fromisoformat(date)
            amount=float(payload['amount']); channel=payload['channel']
            if not data.math.isfinite(amount) or amount<0 or amount>1e12: raise ValueError('投放费用需为 0–1 万亿元')
            if channel not in data.metadata(c,user)['dimensions']['channel']: raise ValueError('渠道不存在')
            c.execute('INSERT OR REPLACE INTO spend VALUES (?,?,?)',[date,channel,amount]); audit(c,user,'spend_upsert',payload); c.commit(); return self.send(200,{'ok':True})
        if path in ('/api/ai/chat','/api/ai/interpret','/api/ai/forecast'):
            audit(c,user,'ai_disabled',{'endpoint':path}); c.commit()
            return self.send(501,{'error':'AI 接入入口已预留，当前未配置模型服务。','code':'AI_NOT_CONFIGURED','enabled':False})
        raise APIError(404,'接口不存在')

    def send_csv(self,rows,fields,name):
        buf=io.StringIO(); writer=csv.writer(buf); writer.writerow(fields)
        for row in rows:
            vals=[]
            for key in fields:
                v=row.get(key)
                if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')): v="'"+v
                vals.append(v)
            writer.writerow(vals)
        return self.send(200,('\ufeff'+buf.getvalue()).encode('utf-8'),'text/csv; charset=utf-8',{'Content-Disposition':f'attachment; filename="{name}"'})

    def static(self,path):
        web=(data.ROOT/'web').resolve()
        relative=path.lstrip('/') or 'index.html'
        file=(web/relative).resolve()
        if not file.is_relative_to(web) or not file.is_file(): raise APIError(404,'文件不存在')
        mime=mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
        if file.suffix=='.js': mime='text/javascript'
        self.send(200,file.read_bytes(),mime+('; charset=utf-8' if mime.startswith('text/') else ''))

def main():
    parser=argparse.ArgumentParser(description='Game Insight local analytics server')
    parser.add_argument('--host',default=os.getenv('HOST','127.0.0.1'))
    parser.add_argument('--port',type=int,default=int(os.getenv('PORT','8765')))
    parser.add_argument('--db',default=os.getenv('DB_PATH',str(data.ROOT/'data'/'insight.db')))
    parser.add_argument('--empty',action='store_true',help='Initialize without demo data')
    parser.add_argument('--open',action='store_true',help='Open the local app in your default browser')
    args=parser.parse_args()
    print('Preparing local database…',flush=True)
    data.initialize(args.db,not args.empty)
    server=AppServer((args.host,args.port),args.db)
    print(f'Game Insight ready: http://{args.host}:{args.port}\nFirst visit: create your administrator account. Ctrl+C stops the server.',flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(f'http://127.0.0.1:{args.port}')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
