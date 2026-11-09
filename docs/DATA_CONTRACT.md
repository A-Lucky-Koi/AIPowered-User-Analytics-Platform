# 数据契约与指标口径

本地版接收已清洗的用户日级汇总，而非原始事件。源系统需要完成设备去重、封禁过滤、时区转换、退款策略与日批次完整性校验。`user_id` 应是稳定的设备标识；若导入账号 ID，DAU 口径会变为账号，必须同步修改指标字典。

## 用户日事实 CSV

每行唯一键 `(user_id,date)`。新增行插入，同键更新；文件内同键重复拒绝。不能导入未来日期。数据仅存本地。

| 列 | 类型 / 示例 | 含义与约束 |
| --- | --- | --- |
| user_id | `device-001` | 非空稳定标识，最多 100 字符 |
| registered | `2026-11-01` | 首次启动日期，ISO YYYY-MM-DD；不得晚于 date |
| channel | `TapTap` | 首次获客渠道，非空，最多 100 字符 |
| os | `Android` | Android / iOS / PC |
| server | `星河一区` | 注册区服，非空，最多 100 字符 |
| version | `V2.1` | **注册版本**，非每日当前客户端版本 |
| date | `2026-11-01` | 活跃日，按 Asia/Shanghai 日历日 |
| minutes | `45.5` | 当日累计在线分钟，0–1440，有限数 |
| sessions | `3` | 当日启动次数，1–10000 的整数 |
| revenue | `68` | 当日累计实付人民币，非负有限数，最多 1e9；不处理退款 |
| level | `15` | 当日达到的最高等级，1–999 整数 |
| stage | `4` | 截至当日完成的最高**有序累计**阶段，1–6；付费日必须为 6 |
| hour | `20` | 当日首次启动小时，0–23 整数 |

阶段依次为：1 首次启动，2 完成注册，3 创建角色，4 完成新手，5 核心玩法，6 首次付费。必须由上游保证累计阶段的真实性和有序语义；本地校验范围与付费条件，不从原始事件重建顺序。

同一用户的 registered/channel/os/server/version 在所有行保持一致，与已导入维度冲突则整个批次失败。首次导入演示库时统一替换演示事实与费用；后续真实数据按唯一键更新。完整校验后在一次数据库事务中提交。

### 完整性约定

默认已导入的最早/最晚事实之间是完整观测窗口。中间日期没有记录，按没有活跃处理，**不能用于推断数据采集失败**。请在上游完成每日对账后再导入。只导入某几日或某一部分用户会影响回流、留存与累计付费；导入不会自动证明数据完整。

窗口外的日指标显示 null / “—”，不足完整 7/30 天历史时 WAU/MAU 显示 “—”。空库显示 0 活跃和无可计算比率；页面标记“等待导入”。

## 计算口径

- DAU：活跃事实中未封禁 user_id 去重；唯一键使 `COUNT(*)` 等价。
- 新增：players.registered 等于该日；独立于是否提供该日事实。
- WAU/MAU：截至结束日的 7/30 个自然日去重，非日活求和。
- 回流：当日前存在历史活跃，但 `[当日-7天, 当日-1天]` 内没有活跃。数据最早日起的新用户不能算回流。
- 付费率：当日 revenue > 0 的设备数 / DAU；分母为 0 返回 null。
- ARPU/ARPPU：流水 / DAU，或流水 / 当日付费设备数。
- Dn 留存：首次启动后第 n 个自然日有活跃。仅观察截止前已满 n 天的注册批次参与；观察截止是 `min(筛选结束日, 数据水位)`。
- 加权留存：成熟批次留存设备合计 / 成熟批次新增合计；算术留存：成熟批次留存率的算术平均（展示精度 2 位）。
- 漏斗：注册期内设备，在首次启动后 0–7 天且不晚于筛选结束日达到的最大 stage；每层计数 `stage>=N`。最近批次可能未满窗口。
- LTV Dn：成熟批次注册后 0–n 天累计实付 / 成熟批次设备数。不同 n 可能使用不同批次，曲线不保证单调。
- 渠道“期间活跃”：日期区间内去重设备，而非日活均值。
- 期间 ROAS：期间该渠道**全部用户**实付 / 同期该渠道费用。不是新增 Cohort 的投资回收率；当前未扣退款、税费和平台分成。
- 费用只有渠道/日粒度；设备、区服、版本切片时不归属费用、不计算 ROAS。无费用或零费用均返回 null 比率。
- 分群：全历史最高 level、累计 revenue、最后活跃日期；截止日为数据水位。固定 AND 组合，不继承顶部日期条件。预览最多 100 行，导出最多 10,000 人，超过则要求收窄条件。

## API 使用

所有 `/api` 数据接口均需登录 Cookie。写接口另需 `X-CSRF-Token`，可从登录响应 user.csrf 取得。JSON UTF-8 请求，错误形如 `{"error":"说明"}`，401 需重新登录，403 无权限。

| 方法与路由 | 主要输入 / 说明 |
| --- | --- |
| GET /api/health | 无鉴权健康检查 |
| GET /api/auth/status | 首次 setup 状态与当前用户 |
| POST /api/auth/setup | username,password；只能首次执行 |
| POST /api/auth/login | username,password；返回 HttpOnly Cookie 与 csrf |
| POST /api/auth/logout | `{}` |
| GET /api/meta | 维度、口径、数据范围、水位、来源 |
| GET /api/analytics | start,end,channel,os,server,version；all 表示不进一步过滤 |
| GET /api/export | 同筛选；BOM UTF-8 CSV 日报，按角色过滤付费字段 |
| GET/POST /api/views | 当前用户看板列表 / name,filters |
| POST /api/views/delete | id；只能删除自己收藏 |
| GET/POST /api/segments | 当前用户规则列表 / name,rules |
| POST /api/segments/preview | rules:{channel,min_level,min_spend,inactive_days} |
| POST /api/segments/export | 同 rules，返回脱敏 CSV |
| POST /api/segments/delete | id；只能删除自己的规则 |
| POST /api/sql | sql,filters；返回 columns,rows,truncated,elapsed_ms |
| POST /api/alerts/ack | key,filters；校验当前范围下告警 |
| GET /api/admin | 管理员：账号、规则、计数、最近审计 |
| GET /api/import-template | 管理员：CSV 模板 |
| POST /api/admin/import | csv（字符串）,confirm:true |
| POST /api/admin/spend | date,channel,amount；同日同渠道覆盖 |
| POST /api/admin/accounts | username,password,role,channel |
| POST /api/admin/rules | metric: dau/new/revenue,threshold:1–1000,enabled |

### SQL Lab

仅支持 SELECT，不能有 WITH、PRAGMA、ATTACH、更新语句或多语句。常用聚合、日期、字符串函数有白名单；结果最多 1,000 行。执行 VM 有 3 秒墙钟和约 500 万指令双预算。计时从 SQL 执行开始，不包含聚合快照准备。即使 SQL 过滤失误，也只能查询服务端已按权限/全局筛选裁剪的数据。`returning` 是 SQLite 保留字，引用该列需双引号：

```sql
SELECT date, dau, "returning" FROM analytics_daily ORDER BY date;
```

当前 schema 是应用语义与日事实模型。要接日志明细、订单明细或实时并发在线，需要新增事实表与查询适配，不应伪造已有字段粒度。
