"""Local analytics repository. All chart values are derived from SQLite facts."""
import csv
import datetime as dt
import hashlib
import io
import json
import math
import random
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHANNELS = ['自然流量', 'TapTap', '抖音', '哔哩哔哩', '应用商店']
STAGES = ['首次启动', '完成注册', '创建角色', '完成新手', '核心玩法', '首次付费']
METRICS = [
    ('dau', 'DAU', '当日启动至少一次且未封禁的去重设备数；本地版要求 user_id 为稳定设备标识。', '设备'),
    ('wau', 'WAU', '截至所选结束日，最近 7 个自然日的去重活跃设备数。', '设备'),
    ('mau', 'MAU', '截至所选结束日，最近 30 个自然日的去重活跃设备数。', '设备'),
    ('new', '新增用户', '当日首次启动的未封禁设备数；registered 为首次启动日。', '设备'),
    ('returning', '回流用户', '此前有活跃记录，连续至少 7 个自然日无活跃后，当日再次活跃。', '设备'),
    ('revenue', '游戏流水', '日事实表中的实付人民币合计；不含退款、税费调整。', '元'),
    ('pay_rate', '付费率', '当日实付金额大于 0 的设备数 / 当日活跃设备数。', '%'),
    ('arpu', 'ARPU', '当日游戏流水 / 当日活跃设备数。', '元'),
    ('arppu', 'ARPPU', '当日游戏流水 / 当日付费设备数。', '元'),
    ('retention_d1', '次日留存', '首次启动后第 1 个自然日活跃的设备 / 已达到观察日的新增设备。', '%'),
    ('retention_d7', '7 日留存', '首次启动后第 7 个自然日活跃的设备 / 已达到观察日的新增设备。', '%'),
    ('roi', '期间 ROAS', '筛选期间渠道全部用户流水 / 同期渠道投放费用；并非同批次回收率。', '%'),
    ('ltv', '累计 LTV', '注册批次截至第 N 天的累计实付 / 已成熟至 N 天的批次新增设备。', '元'),
]

def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')

class Database(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

def connect(path):
    c = sqlite3.connect(path, timeout=10, factory=Database)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def initialize(path, demo=True):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as c:
        c.executescript((ROOT / 'schema.sql').read_text('utf-8'))
        c.execute('PRAGMA journal_mode=WAL')
        for metric, threshold in [('dau', 8), ('new', 15), ('revenue', 20)]:
            c.execute('INSERT OR IGNORE INTO alert_rules(metric,threshold) VALUES (?,?)', (metric,threshold))
        if not c.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone():
            if demo:
                seed(c)
            else:
                c.executemany('INSERT INTO meta VALUES (?,?)', [('source','empty'), ('updated',now())])
            c.execute("INSERT INTO meta VALUES ('initialized','1')")

def seed(c, end=None):
    rng = random.Random(20261109)
    end = end or (dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date() - dt.timedelta(days=1))
    start = end - dt.timedelta(days=89)
    users, facts, costs = [], [], []
    uid = 0
    for d in range(90):
        date = start + dt.timedelta(days=d)
        for ci, channel in enumerate(CHANNELS):
            count = int((18 + ci*5) * (1+d/150) * (1+.18*math.sin(d*.7)) * rng.uniform(.85,1.15))
            if d >= 86 and ci == 2:
                count = int(count*.45)
            costs.append((date.isoformat(), channel, round(count*(0 if ci==0 else [0,11,14,9,12][ci])*rng.uniform(.85,1.15),2)))
            for _ in range(count):
                uid += 1
                key = f'D{uid:07d}'
                os = rng.choices(['Android','iOS','PC'],[.63,.29,.08])[0]
                server = rng.choice(['星河一区','晨曦二区','远征三区'])
                version = 'V2.1' if d >= 65 else 'V2.0'
                users.append((key,date.isoformat(),channel,os,server,version,0))
                affinity = rng.uniform(.5,1.5)
                progress = rng.choices([1,2,3,4,5],[.06,.09,.13,.16,.56])[0]
                for age in range(90-d):
                    day = date + dt.timedelta(days=age)
                    chance = (.13+.43*math.exp(-age/6)) * affinity * [1.12,1.14,.82,1.05,.96][ci]
                    if d+age >= 87 and ci==2:
                        chance *= .65
                    if age and rng.random()>chance:
                        continue
                    mins = round(min(360,max(3,rng.gauss(63,30)*affinity)),1)
                    money = rng.choices([6,30,68,128,328,648],[20,32,26,15,5,2])[0] if progress>=4 and rng.random()<.055*affinity else 0
                    if money:
                        progress=6
                    facts.append((key,day.isoformat(),mins,rng.randint(1,6),money,min(100,1+age//2+int(mins/12)),progress,rng.choices([8,10,12,14,16,18,20,21,22,23],[4,5,8,7,9,16,22,18,8,3])[0]))
    c.executemany('INSERT INTO players VALUES (?,?,?,?,?,?,?)',users)
    c.executemany('INSERT INTO activity VALUES (?,?,?,?,?,?,?,?)',facts)
    c.executemany('INSERT INTO spend VALUES (?,?,?)',costs)
    c.executemany('INSERT INTO meta VALUES (?,?)',[('source','demo'),('updated',now()),('seed','20261109'),('watermark',end.isoformat())])

def metadata(c, account):
    meta = dict(c.execute('SELECT key,value FROM meta'))
    where, params = scope({},account)
    bounds = c.execute(f'SELECT MIN(a.date),MAX(a.date) FROM activity a JOIN players u USING(user_id) WHERE {where}',params).fetchone()
    dims = {}
    for col in ['channel','os','server','version']:
        dims[col] = [r[0] for r in c.execute(f'SELECT DISTINCT u.{col} FROM players u WHERE {where} ORDER BY 1',params)]
    return {**meta,'min_date':bounds[0],'max_date':bounds[1],'dimensions':dims,'metrics':[dict(zip(['key','name','definition','unit'],m), cadence='T+1 / 手动导入') for m in METRICS], 'ai_enabled':False}

def parse_filters(raw, meta):
    end = raw.get('end') or meta.get('max_date') or dt.date.today().isoformat()
    start = raw.get('start') or (dt.date.fromisoformat(end)-dt.timedelta(days=13)).isoformat()
    s,e = dt.date.fromisoformat(start),dt.date.fromisoformat(end)
    if e<s or (e-s).days>365:
        raise ValueError('日期范围必须为 1–366 天，且开始日不晚于结束日')
    return {'start':start,'end':end, **{k:raw.get(k,'all') for k in ['channel','os','server','version']}}

def scope(f, account):
    clauses, params = ['u.banned=0'], []
    for key in ['channel','os','server','version']:
        val=f.get(key,'all')
        if val != 'all':
            clauses.append(f'u.{key}=?'); params.append(val)
    if account.get('channel','all') != 'all':
        clauses.append('u.channel=?'); params.append(account['channel'])
    return ' AND '.join(clauses),params

def ratio(a,b,mult=1):
    return round(a/b*mult,2) if b else None

def analytics(c,f,account):
    where,params=scope(f,account)
    end=dt.date.fromisoformat(f['end'])
    start=dt.date.fromisoformat(f['start'])
    query_start=min(start,end-dt.timedelta(days=1)).isoformat()
    rows=c.execute(f'''SELECT a.date,COUNT(*) dau,SUM(a.revenue) revenue,
        SUM(a.revenue>0) payers,SUM(a.minutes) minutes,SUM(a.sessions) sessions,
        SUM(u.registered=a.date) new,
        SUM(EXISTS(SELECT 1 FROM activity old WHERE old.user_id=a.user_id AND old.date<a.date)
            AND NOT EXISTS(SELECT 1 FROM activity prev WHERE prev.user_id=a.user_id AND prev.date<a.date AND prev.date>=date(a.date,'-7 days'))) AS "returning"
        FROM activity a JOIN players u USING(user_id) WHERE {where} AND a.date BETWEEN ? AND ? GROUP BY a.date ORDER BY a.date''',params+[query_start,f['end']]).fetchall()
    bydate={r['date']:dict(r) for r in rows}
    births={r[0]:r[1] for r in c.execute(f'SELECT u.registered,COUNT(*) FROM players u WHERE {where} AND u.registered BETWEEN ? AND ? GROUP BY u.registered',params+[query_start,f['end']])}
    coverage=c.execute('SELECT MIN(date),MAX(date) FROM activity').fetchone()
    def day(date):
        key=date.isoformat()
        r=bydate.get(key,{'date':key,'dau':0,'revenue':0,'payers':0,'minutes':0,'sessions':0,'new':0,'returning':0})
        if coverage[0] and not coverage[0]<=key<=coverage[1]:
            return {'date':key,**{k:None for k in ['dau','revenue','payers','minutes','sessions','new','returning','pay_rate','arpu','arppu','avg_minutes']}}
        return {**r,'new':births.get(key,0),'pay_rate':ratio(r['payers'],r['dau'],100),'arpu':ratio(r['revenue'],r['dau']),'arppu':ratio(r['revenue'],r['payers']),'avg_minutes':ratio(r['minutes'],r['dau'])}
    current,prev=day(end),day(end-dt.timedelta(days=1))
    for n,label in [(7,'wau'),(30,'mau')]:
        for obj,d in [(current,end),(prev,end-dt.timedelta(days=1))]:
            obj[label]=c.execute(f'SELECT COUNT(DISTINCT a.user_id) FROM activity a JOIN players u USING(user_id) WHERE {where} AND a.date BETWEEN ? AND ?',params+[(d-dt.timedelta(days=n-1)).isoformat(),d.isoformat()]).fetchone()[0]
            if coverage[0] and ((d-dt.timedelta(days=n-1)).isoformat()<coverage[0] or d.isoformat()>coverage[1]): obj[label]=None
    trend=[day(start+dt.timedelta(days=i)) for i in range((end-start).days+1)]
    cohort=retention(c,f,account)
    channels=channel_stats(c,f,account,cohort)
    histogram=c.execute(f'''SELECT CASE WHEN a.minutes<15 THEN '0–15 分钟' WHEN a.minutes<30 THEN '15–30 分钟' WHEN a.minutes<60 THEN '30–60 分钟' WHEN a.minutes<120 THEN '60–120 分钟' ELSE '120+ 分钟' END name,COUNT(*) value,MIN(a.minutes) sort
        FROM activity a JOIN players u USING(user_id) WHERE {where} AND a.date=? GROUP BY name ORDER BY sort''',params+[f['end']]).fetchall()
    hours=c.execute(f'SELECT a.hour,COUNT(*) value FROM activity a JOIN players u USING(user_id) WHERE {where} AND a.date=? GROUP BY a.hour',params+[f['end']]).fetchall()
    devices=c.execute(f'SELECT u.os name,COUNT(*) value FROM activity a JOIN players u USING(user_id) WHERE {where} AND a.date=? GROUP BY u.os',params+[f['end']]).fetchall()
    paytiers=c.execute(f'''SELECT CASE WHEN a.revenue=0 THEN '未付费' WHEN a.revenue<30 THEN '1–29 元' WHEN a.revenue<100 THEN '30–99 元' WHEN a.revenue<300 THEN '100–299 元' ELSE '300+ 元' END name,COUNT(*) value,SUM(a.revenue) revenue,MIN(a.revenue) sort
        FROM activity a JOIN players u USING(user_id) WHERE {where} AND a.date=? GROUP BY name ORDER BY sort''',params+[f['end']]).fetchall()
    total=c.execute(f'SELECT COUNT(DISTINCT a.user_id) active,COALESCE(SUM(a.revenue),0) revenue,COALESCE(SUM(a.sessions),0) sessions FROM activity a JOIN players u USING(user_id) WHERE {where} AND a.date BETWEEN ? AND ?',params+[f['start'],f['end']]).fetchone()
    result={'current':current,'previous':prev,'trend':trend,'retention':cohort,'channels':channels,'duration':[dict(r) for r in histogram], 'hours':[dict(r) for r in hours], 'devices':[dict(r) for r in devices], 'paytiers':[dict(r) for r in paytiers], 'total':dict(total),'funnel':funnel(c,f,account), 'ltv':ltv(c,f,account),'filters':f,'computed_at':now(),'coverage':{'start':coverage[0],'end':coverage[1]}}
    result['alerts']=alerts(c,f,account,current,prev)
    if account['role']=='operations':
        redact(result)
        result['funnel']=[r for r in result['funnel'] if r['stage']<6]
    return result

def redact(obj):
    sensitive={'revenue','payers','pay_rate','arpu','arppu','spend','roas','ltv','paytiers'}
    if isinstance(obj,dict):
        for k,v in list(obj.items()):
            if k in sensitive:
                obj[k]=None
            else: redact(v)
    elif isinstance(obj,list):
        for v in obj: redact(v)

def retention(c,f,account):
    where,params=scope(f,account)
    watermark=c.execute("SELECT value FROM meta WHERE key='watermark'").fetchone()
    observed=min(f['end'],watermark[0]) if watermark else f['end']
    births=c.execute(f'SELECT u.registered,COUNT(*) n FROM players u WHERE {where} AND u.registered BETWEEN ? AND ? GROUP BY u.registered ORDER BY u.registered',params+[f['start'],f['end']]).fetchall()
    counts=c.execute(f'''SELECT u.registered,CAST(julianday(a.date)-julianday(u.registered) AS INTEGER) age,COUNT(*) n
        FROM activity a JOIN players u USING(user_id) WHERE {where} AND u.registered BETWEEN ? AND ? AND a.date<=? GROUP BY u.registered,age''',params+[f['start'],f['end'],observed]).fetchall()
    lookup={(r['registered'],r['age']):r['n'] for r in counts}
    days=[1,2,3,7,14,30]
    out=[]
    for r in births:
        age=(dt.date.fromisoformat(observed)-dt.date.fromisoformat(r['registered'])).days
        values=[ratio(lookup.get((r['registered'],d),0),r['n'],100) if age>=d else None for d in days]
        out.append({'date':r['registered'],'size':r['n'],'values':values})
    weighted=[]; arithmetic=[]
    for i in range(len(days)):
        mature=[r for r in out if r['values'][i] is not None]
        weighted.append(ratio(sum(lookup.get((r['date'],days[i]),0) for r in mature),sum(r['size'] for r in mature),100))
        arithmetic.append(ratio(sum(r['values'][i] for r in mature),len(mature)))
    return {'days':days,'rows':out,'weighted':weighted,'arithmetic':arithmetic,'observed_through':observed}

def channel_stats(c,f,account,cohort=None):
    where,params=scope(f,account)
    rows=c.execute(f'''SELECT u.channel name,COUNT(DISTINCT a.user_id) active,COUNT(*) active_days,COALESCE(SUM(a.revenue),0) revenue,
        COUNT(DISTINCT CASE WHEN a.revenue>0 THEN a.user_id END) payers
        FROM activity a JOIN players u USING(user_id) WHERE {where} AND a.date BETWEEN ? AND ? GROUP BY u.channel ORDER BY active DESC''',params+[f['start'],f['end']]).fetchall()
    result=[]
    # Spend has channel/day grain; dimension slices cannot claim attributable ROI.
    can_spend=all(f.get(k,'all')=='all' for k in ['os','server','version'])
    for row in rows:
        r=dict(row)
        cf={**f,'channel':r['name']}
        w,p=scope(cf,account)
        r['new']=c.execute(f'SELECT COUNT(*) FROM players u WHERE {w} AND u.registered BETWEEN ? AND ?',p+[f['start'],f['end']]).fetchone()[0]
        ret=retention(c,cf,account)
        r['d1'],r['d7']=ret['weighted'][0],ret['weighted'][3]
        r['spend']=c.execute('SELECT SUM(amount) FROM spend WHERE channel=? AND date BETWEEN ? AND ?',[r['name'],f['start'],f['end']]).fetchone()[0] if can_spend else None
        r['roas']=ratio(r['revenue'],r['spend'],100) if r['spend'] else None
        r['arpu']=ratio(r['revenue'],r['active'])
        result.append(r)
    return result

def funnel(c,f,account):
    where,params=scope(f,account)
    rows=c.execute(f'''SELECT u.user_id,COALESCE(MAX(a.stage),1) stage FROM players u LEFT JOIN activity a ON u.user_id=a.user_id AND a.date<=date(u.registered,'+7 days') AND a.date<=?
        WHERE {where} AND u.registered BETWEEN ? AND ? GROUP BY u.user_id''',[f['end']]+params+[f['start'],f['end']]).fetchall()
    return [{'stage':i+1,'name':s,'value':sum(r['stage']>=i+1 for r in rows)} for i,s in enumerate(STAGES)]

def ltv(c,f,account):
    where,params=scope(f,account)
    watermark=c.execute("SELECT value FROM meta WHERE key='watermark'").fetchone()
    observed=min(f['end'],watermark[0]) if watermark else f['end']
    result=[]
    for day in [0,1,3,7,14,30]:
        mature=(dt.date.fromisoformat(observed)-dt.timedelta(days=day)).isoformat()
        r=c.execute(f'''SELECT COUNT(DISTINCT u.user_id) n,COALESCE(SUM(a.revenue),0) revenue FROM players u LEFT JOIN activity a ON u.user_id=a.user_id AND a.date<=date(u.registered,'+{day} days')
            WHERE {where} AND u.registered BETWEEN ? AND ? AND u.registered<=?''',params+[f['start'],f['end'],mature]).fetchone()
        result.append({'day':day,'size':r['n'],'value':ratio(r['revenue'],r['n'])})
    return result

def alerts(c,f,account,current,previous):
    result=[]
    for rule in c.execute('SELECT * FROM alert_rules WHERE enabled=1'):
        metric=rule['metric']
        if metric=='revenue' and account['role']=='operations': continue
        a,b=current[metric],previous[metric]
        if a is None or b is None: continue
        delta=ratio(a-b,b,100)
        if delta is None or abs(delta)<rule['threshold']: continue
        key=hashlib.sha256(json.dumps([f,metric,account.get('channel'),account.get('id')],sort_keys=True).encode()).hexdigest()[:24]
        w,p=scope(f,account)
        expr={'dau':'COUNT(*)','new':'SUM(u.registered=a.date)','revenue':'SUM(a.revenue)'}[metric]
        compare_dates=[f['end'],(dt.date.fromisoformat(f['end'])-dt.timedelta(days=1)).isoformat()]
        if metric=='new':
            rows=c.execute(f'SELECT u.channel,u.registered date,COUNT(*) value FROM players u WHERE {w} AND u.registered IN (?,?) GROUP BY u.channel,u.registered',p+compare_dates).fetchall()
        else:
            rows=c.execute(f'SELECT u.channel,a.date,{expr} value FROM activity a JOIN players u USING(user_id) WHERE {w} AND a.date IN (?,?) GROUP BY u.channel,a.date',p+compare_dates).fetchall()
        parts={}
        for r in rows:
            parts[r['channel']]=parts.get(r['channel'],0)+r['value']*(1 if r['date']==f['end'] else -1)
        result.append({'key':key,'metric':metric,'name':next(m[1] for m in METRICS if m[0]==metric),'delta':delta,'value':a,'previous':b,'threshold':rule['threshold'],'acknowledged':bool(c.execute('SELECT 1 FROM alert_ack WHERE key=? AND account_id=?',[key,account.get('id')]).fetchone()),'contributions':[{'name':k,'change':round(v,2),'contribution':ratio(v,a-b,100)} for k,v in sorted(parts.items(),key=lambda x:abs(x[1]),reverse=True)]})
    return result

def segment_members(c,rules,account):
    w,p=scope({'channel':rules.get('channel','all')},account)
    min_level=int(rules.get('min_level',1)); min_spend=float(rules.get('min_spend',0)); inactive=int(rules.get('inactive_days',0))
    if min_level<1 or min_level>999 or not math.isfinite(min_spend) or min_spend<0 or not 0<=inactive<=366:
        raise ValueError('分群条件超出允许范围')
    end=c.execute("SELECT value FROM meta WHERE key='watermark'").fetchone()
    end=end[0] if end else dt.date.today().isoformat()
    cutoff=(dt.date.fromisoformat(end)-dt.timedelta(days=inactive)).isoformat()
    rows=c.execute(f'''SELECT u.user_id,u.channel,u.os,u.server,MAX(a.level) level,MAX(a.date) last_active,SUM(a.revenue) ltv,ROUND(AVG(a.minutes),1) avg_minutes
        FROM players u JOIN activity a USING(user_id) WHERE {w} GROUP BY u.user_id HAVING MAX(a.level)>=? AND SUM(a.revenue)>=? AND MAX(a.date)<=? ORDER BY ltv DESC LIMIT 10001''',p+[min_level,min_spend,cutoff]).fetchall()
    if len(rows)>10000: raise ValueError('分群超过 10,000 人，请收窄条件')
    return [{'user':hashlib.sha256(r['user_id'].encode()).hexdigest()[:12],**{k:r[k] for k in r.keys() if k!='user_id'}} for r in rows]

IMPORT_FIELDS=['user_id','registered','channel','os','server','version','date','minutes','sessions','revenue','level','stage','hour']

def import_csv(c,text):
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not reader.fieldnames or not set(IMPORT_FIELDS).issubset(reader.fieldnames):
        raise ValueError('CSV 缺少列：'+', '.join(IMPORT_FIELDS))
    rows=list(reader)
    if not 1<=len(rows)<=50000: raise ValueError('每次需导入 1–50,000 行')
    players={}; facts=[]; keys=set()
    for i,r in enumerate(rows,2):
        try:
            missing=[key for key in IMPORT_FIELDS if r.get(key) is None]
            if missing: raise ValueError('缺少字段值：'+', '.join(missing))
            reg=dt.date.fromisoformat(r['registered']); day=dt.date.fromisoformat(r['date'])
            if reg>day: raise ValueError('活跃日期早于首次启动日')
            if day>dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date(): raise ValueError('不能导入未来日期')
            if r['os'] not in ('Android','iOS','PC'): raise ValueError('os 需为 Android / iOS / PC')
            for key in ['user_id','channel','server','version']:
                if not r[key].strip() or len(r[key])>100: raise ValueError(key+' 不能为空或超过 100 字符')
            minutes,revenue=float(r['minutes']),float(r['revenue'])
            sessions,level,stage,hour=[int(r[k]) for k in ['sessions','level','stage','hour']]
            if not all(math.isfinite(v) and 0<=v<=1e9 for v in [minutes,revenue]) or minutes>1440 or not 1<=sessions<=10000 or not 1<=level<=999 or not 1<=stage<=6 or not 0<=hour<24:
                raise ValueError('数值超出范围')
            if revenue>0 and stage!=6: raise ValueError('付费日 stage 必须为 6')
            uid=r['user_id']; key=(uid,day.isoformat())
            if key in keys: raise ValueError('文件内重复 user_id + date')
            keys.add(key)
            player=(uid,reg.isoformat(),r['channel'],r['os'],r['server'],r['version'],0)
            if uid in players and players[uid]!=player: raise ValueError('同一用户的注册维度不一致')
            players[uid]=player
            facts.append((uid,day.isoformat(),minutes,sessions,revenue,level,stage,hour))
        except (ValueError,TypeError) as exc:
            raise ValueError(f'第 {i} 行：{exc}') from exc
    # Validation precedes the transaction. A real-data import replaces demo facts atomically.
    source=c.execute("SELECT value FROM meta WHERE key='source'").fetchone()
    is_demo=source and source[0]=='demo'
    if not is_demo:
        for uid,player in players.items():
            existing=c.execute('SELECT * FROM players WHERE user_id=?',[uid]).fetchone()
            if existing and tuple(existing)[:6]!=player[:6]: raise ValueError(f'用户 {uid} 的注册维度与库内冲突')
    if is_demo:
        c.execute('DELETE FROM activity'); c.execute('DELETE FROM players'); c.execute('DELETE FROM spend'); c.execute('DELETE FROM alert_ack')
    c.executemany('INSERT OR IGNORE INTO players VALUES (?,?,?,?,?,?,?)',players.values())
    c.executemany('INSERT INTO activity VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(user_id,date) DO UPDATE SET minutes=excluded.minutes,sessions=excluded.sessions,revenue=excluded.revenue,level=excluded.level,stage=excluded.stage,hour=excluded.hour',facts)
    c.execute("INSERT OR REPLACE INTO meta VALUES ('source','imported')")
    c.execute("INSERT OR REPLACE INTO meta VALUES ('updated',?)",[now()])
    c.execute("INSERT OR REPLACE INTO meta SELECT 'watermark',MAX(date) FROM activity")
    return {'rows':len(facts),'players':len(players),'replaced_demo':bool(is_demo)}

