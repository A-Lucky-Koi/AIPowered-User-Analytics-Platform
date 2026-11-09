import csv
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import data
import server

ADMIN={'id':1,'role':'admin','channel':'all'}
ADMIN2={'id':2,'role':'admin','channel':'all'}
ANALYST={'id':2,'role':'analyst','channel':'TapTap'}
OPS={'id':3,'role':'operations','channel':'all'}

class AnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.db'
        data.initialize(self.path,False)
        self.c=data.connect(self.path)
        self.c.executemany('INSERT INTO accounts(id,username,password,role,channel,created) VALUES (?,?,?,?,?,?)',[
            (1,'admin-one','test','admin','all',data.now()),
            (2,'admin-two','test','admin','all',data.now()),
            (3,'ops','test','operations','all',data.now()),
        ])
        self.c.executemany('INSERT INTO players VALUES (?,?,?,?,?,?,?)',[
            ('a','2026-10-01','TapTap','Android','一区','V2',0),
            ('b','2026-10-01','自然流量','iOS','二区','V1',0),
            ('c','2026-10-02','TapTap','Android','一区','V2',0),
            ('x','2026-10-01','TapTap','Android','一区','V2',1),
        ])
        self.c.executemany('INSERT INTO activity VALUES (?,?,?,?,?,?,?,?)',[
            ('a','2026-10-01',50,2,6,10,6,20),('a','2026-10-02',40,1,0,12,6,12),('a','2026-10-10',70,3,30,20,6,20),
            ('b','2026-10-01',20,1,0,2,3,12),('b','2026-10-08',30,2,0,5,4,12),('b','2026-10-09',25,1,0,6,4,12),
            ('c','2026-10-02',90,3,68,10,6,20),('c','2026-10-03',20,1,0,12,6,12),('c','2026-10-10',50,2,0,20,6,20),
            ('x','2026-10-10',50,2,999,100,6,20),
        ])
        self.c.execute("INSERT OR REPLACE INTO meta VALUES ('watermark','2026-10-10')")
        self.c.execute("INSERT INTO spend VALUES ('2026-10-10','TapTap',20)")
        self.c.commit()
        self.f={'start':'2026-10-01','end':'2026-10-10','channel':'all','os':'all','server':'all','version':'all'}
    def tearDown(self): self.c.close(); self.temp.cleanup()
    def test_metrics_and_banned_users(self):
        r=data.analytics(self.c,self.f,ADMIN)
        self.assertEqual(r['current']['dau'],2)
        self.assertEqual(r['current']['revenue'],30)
        self.assertEqual(r['current']['arpu'],15)
        self.assertEqual(r['current']['pay_rate'],50)
        self.assertEqual(r['current']['returning'],1) # c was active exactly 7 days ago: only six empty days.
        self.assertEqual(r['current']['wau'],3)
        self.assertIsNone(r['current']['mau']) # Only 10 observed days: do not claim a complete 30-day window.
        self.assertEqual(r['total']['revenue'],104)
        self.assertEqual(r['total']['active'],3)
    def test_retention_maturity_weighting(self):
        f={**self.f,'end':'2026-10-02'}
        r=data.retention(self.c,f,ADMIN)
        self.assertEqual(r['rows'][0]['values'][0],50)
        self.assertIsNone(r['rows'][1]['values'][0])
        self.assertIsNone(r['rows'][0]['values'][1])
        self.assertEqual(r['weighted'][0],50)
        later=data.retention(self.c,self.f,ADMIN)
        self.assertEqual(later['weighted'][0],66.67)
        self.assertEqual(later['arithmetic'][0],75)
        self.assertEqual(later['weighted'][3],33.33)
    def test_missing_coverage_is_not_fabricated_as_zero(self):
        r=data.analytics(self.c,{**self.f,'end':'2026-10-11'},ADMIN)
        self.assertIsNone(r['current']['dau'])
        self.assertIsNone(r['current']['wau'])
        self.assertEqual(r['alerts'],[])
        self.assertEqual(r['trend'][-2]['dau'],2)
    def test_channel_scope_cannot_be_overridden(self):
        r=data.analytics(self.c,self.f,ANALYST)
        self.assertEqual([x['name'] for x in r['channels']],['TapTap'])
        self.assertEqual(r['total']['active'],2)
        r=data.analytics(self.c,{**self.f,'channel':'自然流量'},ANALYST)
        self.assertEqual(r['total']['active'],0)
        self.assertEqual(data.segment_members(self.c,{'channel':'自然流量'},ANALYST),[])
    def test_operations_redaction(self):
        r=data.analytics(self.c,self.f,OPS)
        self.assertIsNone(r['current']['revenue'])
        self.assertIsNone(r['current']['payers'])
        self.assertIsNone(r['total']['revenue'])
        self.assertIsNone(r['ltv'])
        self.assertIsNone(r['channels'][0]['spend'])
        self.assertTrue(all(x['metric']!='revenue' for x in r['alerts']))
    def test_alert_acknowledgement_is_account_scoped(self):
        first=data.analytics(self.c,self.f,ADMIN)
        alert=next(a for a in first['alerts'] if a['metric']=='dau')
        self.c.execute('INSERT INTO alert_ack VALUES (?,?,?)',[alert['key'],ADMIN['id'],data.now()])
        self.c.commit()
        first_again=next(a for a in data.analytics(self.c,self.f,ADMIN)['alerts'] if a['metric']=='dau')
        second=next(a for a in data.analytics(self.c,self.f,ADMIN2)['alerts'] if a['metric']=='dau')
        self.assertTrue(first_again['acknowledged'])
        self.assertNotEqual(first_again['key'],second['key'])
        self.assertFalse(second['acknowledged'])
    def test_spend_slice_not_misattributed(self):
        r=data.channel_stats(self.c,{**self.f,'os':'Android'},ADMIN)
        self.assertTrue(all(x['spend'] is None and x['roas'] is None for x in r))
    def test_funnel_monotonic_and_ltv(self):
        values=[r['value'] for r in data.funnel(self.c,self.f,ADMIN)]
        self.assertEqual(values,sorted(values,reverse=True))
        self.assertEqual(values[0],3)
        self.assertEqual(values[-1],2)
        rows=data.ltv(self.c,self.f,ADMIN)
        self.assertEqual(rows[0]['value'],24.67)
        self.assertIsNone(rows[-1]['value'])
    def test_sql_scoped_and_readonly(self):
        result=server.safe_sql(self.c,'SELECT channel, active FROM analytics_channels',self.f,ANALYST)
        self.assertEqual(result['rows'],[['TapTap',2]])
        for sql in ['DELETE FROM analytics_daily','SELECT * FROM accounts','SELECT * FROM sqlite_master',"SELECT load_extension('x')",'SELECT * FROM analytics_daily; DELETE FROM analytics_daily','SELECT randomblob(1000000000)','SELECT * FROM pragma_database_list']:
            with self.subTest(sql=sql),self.assertRaises((ValueError,sqlite3.Error)):
                server.safe_sql(self.c,sql,self.f,ADMIN)
        with self.assertRaises(server.APIError): server.safe_sql(self.c,'SELECT 1',self.f,OPS)
    def test_sql_deadline_and_row_cap(self):
        result=server.safe_sql(self.c,'SELECT a.date FROM analytics_daily a, analytics_daily b, analytics_daily c, analytics_daily d',self.f,ADMIN)
        self.assertEqual(len(result['rows']),1000)
        self.assertTrue(result['truncated'])
        with self.assertRaises(sqlite3.OperationalError):
            server.safe_sql(self.c,'SELECT sum(a.dau) FROM analytics_daily a, analytics_daily b, analytics_daily c, analytics_daily d, analytics_daily e, analytics_daily f, analytics_daily g, analytics_daily h',self.f,ADMIN)
    def test_group_scope_and_masking(self):
        rows=data.segment_members(self.c,{'min_level':15,'min_spend':1,'inactive_days':0},ANALYST)
        self.assertEqual(len(rows),2)
        self.assertNotEqual(rows[0]['user'],'a')
        self.assertNotIn('user_id',rows[0])
        self.assertEqual(sorted(r['ltv'] for r in rows),[36,68])
    def csv(self,changes=None):
        row=dict(zip(data.IMPORT_FIELDS,['new01','2026-10-05','TapTap','Android','一区','V2','2026-10-05','20','1','0','2','3','12']))
        row.update(changes or {})
        buf=io.StringIO(); writer=csv.DictWriter(buf,fieldnames=data.IMPORT_FIELDS);writer.writeheader();writer.writerow(row)
        return buf.getvalue()
    def test_import_rejects_invalid_without_changes(self):
        before=self.c.execute('SELECT COUNT(*) FROM activity').fetchone()[0]
        for changes in [{'revenue':'nan'},{'revenue':'-3'},{'stage':'8'},{'os':'other'},{'date':'2026-10-01'},{'revenue':'6','stage':'4'},{'hour':'25'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError): data.import_csv(self.c,self.csv(changes))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM activity').fetchone()[0],before)
    def test_import_missing_trailing_value_has_row_error(self):
        row=dict(zip(data.IMPORT_FIELDS,['new01','2026-10-05','TapTap','Android','一区','V2','2026-10-05','20','1','0','2','3','12']))
        fields=data.IMPORT_FIELDS[1:]+['user_id']
        buf=io.StringIO(); writer=csv.writer(buf); writer.writerow(fields); writer.writerow([row[k] for k in fields[:-1]])
        with self.assertRaisesRegex(ValueError,'第 2 行：缺少字段值：user_id'):
            data.import_csv(self.c,buf.getvalue())
    def test_import_upsert_and_dimension_conflict(self):
        data.import_csv(self.c,self.csv());data.import_csv(self.c,self.csv({'minutes':'80'}))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM activity WHERE user_id='new01'").fetchone()[0],1)
        self.assertEqual(self.c.execute("SELECT minutes FROM activity WHERE user_id='new01'").fetchone()[0],80)
        with self.assertRaises(ValueError): data.import_csv(self.c,self.csv({'channel':'other'}))
    def test_first_import_replaces_demo(self):
        self.c.execute("UPDATE meta SET value='demo' WHERE key='source'")
        result=data.import_csv(self.c,self.csv())
        self.assertTrue(result['replaced_demo'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM activity').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM spend').fetchone()[0],0)
    def test_validation(self):
        for raw in [{'start':'2026-11-10','end':'2026-11-01'},{'start':'2020-01-01','end':'2026-11-01'},{'start':'bad'}]:
            with self.assertRaises(ValueError): data.parse_filters(raw,{})
        with self.assertRaises(ValueError): data.segment_members(self.c,{'min_spend':'nan'},ADMIN)

if __name__=='__main__': unittest.main()
