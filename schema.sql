PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS players (
  user_id TEXT PRIMARY KEY, registered TEXT NOT NULL, channel TEXT NOT NULL,
  os TEXT NOT NULL CHECK(os IN ('Android','iOS','PC')), server TEXT NOT NULL,
  version TEXT NOT NULL, banned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activity (
  user_id TEXT NOT NULL REFERENCES players(user_id), date TEXT NOT NULL,
  minutes REAL NOT NULL CHECK(minutes >= 0), sessions INTEGER NOT NULL CHECK(sessions > 0),
  revenue REAL NOT NULL CHECK(revenue >= 0), level INTEGER NOT NULL CHECK(level > 0),
  stage INTEGER NOT NULL CHECK(stage BETWEEN 1 AND 6), hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23),
  PRIMARY KEY(user_id,date)
);
CREATE INDEX IF NOT EXISTS idx_activity_date ON activity(date);
CREATE INDEX IF NOT EXISTS idx_players_scope ON players(channel,os,server,version,registered);
CREATE INDEX IF NOT EXISTS idx_players_registered ON players(registered,user_id);
CREATE TABLE IF NOT EXISTS spend (date TEXT NOT NULL, channel TEXT NOT NULL, amount REAL NOT NULL CHECK(amount >= 0), PRIMARY KEY(date,channel));
CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'all', created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), csrf TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS saved_views (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, name TEXT NOT NULL, filters TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS segments (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, name TEXT NOT NULL, rules TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alert_rules (metric TEXT PRIMARY KEY, threshold REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS alert_ack (key TEXT PRIMARY KEY, account_id INTEGER NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, account_id INTEGER, action TEXT NOT NULL, detail TEXT NOT NULL, created TEXT NOT NULL);
