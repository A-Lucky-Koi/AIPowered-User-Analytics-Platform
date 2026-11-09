# Game Insight · 游戏用户数据分析平台

按照《线上游戏用户数据分析可视化平台 V1.0》实现的**可离线运行的本地单机版**。Vue 3 + ECharts 前端、Python API、SQLite 持久化。AI 入口和接口契约保留，当前不调用模型、不生成模拟 AI 答案。

## 立即启动

需要 **Python 3.10 或更高版本**。无需 Node.js、npm、pip 安装，也不需要数据库服务。

**Windows：双击 `start.cmd`**，自动启动服务并打开默认浏览器。保持控制台开启；按 `Ctrl+C` 停止。

也可在此目录运行：

```powershell
python server.py
```

访问 **http://127.0.0.1:8765**，首次访问自行创建管理员账号（密码至少 10 个字符）。没有内置生产账号或默认密码。

macOS / Linux：

```bash
sh start.sh
```

首次运行约数秒，会生成 90 天、约 1.6 万设备、13.7 万条日事实的演示数据。固定随机种子 `20261109`，截止日期为初始化当天的前一日（Asia/Shanghai）；数据库存在后不会重置。演示数据有显著标签。前端依赖已本地化，断网也可运行。

## Docker 部署（可选）

```bash
docker compose up -d --build
docker compose logs -f
docker compose down
```

访问同一地址。命名卷 `insight-data` 持久化数据，普通 `down` 不删除；`down -v` 会删除数据卷，请谨慎使用。首次构建需拉取 Python 官方镜像。Docker 文件已提供，但本次环境未执行容器构建，已验证的是 Python 直接启动路径。

## 已实现

| 模块 | 可用能力 |
| --- | --- |
| 工作台 | 收藏、打开和移除看板，保留页面与全局筛选；账号间隔离 |
| 总览大盘 | 单日 DAU、新增、流水、付费率、前日环比、日趋势、渠道表现、设备分布、规则摘要 |
| 活跃分析 | DAU / WAU / MAU 精确去重、回流、时长分布、首次活跃小时分布 |
| 留存分析 | D1 / D2 / D3 / D7 / D14 / D30 热力表，加权/算术留存、曲线、未成熟值处理 |
| 转化漏斗 | 六阶段、7 天转化窗口、可选步骤、相邻选中步骤转化与流失 |
| 付费分析 | 付费率、ARPU、ARPPU、实付档位、成熟批次累计 LTV |
| 渠道分析 | 新增 × 次留象限、质量表、期间流水/投放 ROAS、渠道下钻 |
| 用户分群 | 渠道 AND 等级 AND 累计实付 AND 未活跃天数、规则保存、预览及脱敏 CSV |
| SQL Lab | 权限范围内的聚合语义快照、只读 SELECT、查询超时、1,000 行上限、导出与审计 |
| 系统管理 | 指标字典、CSV 导入、成本维护、账号角色、渠道权限、告警阈值、审计 |
| 告警 | 日环比阈值、渠道算术贡献拆解、处理状态；页面打开时每分钟查询 |
| AI | 分析中心、侧边助手、解读与 SQL 辅助入口、明确的未配置响应及接口文档 |

通用支持：日期/渠道/设备/区服/注册版本筛选、URL 分享与浏览器前进后退、CSV、图表 PNG、图表数据表替代视图、移动端导航、键盘焦点控制、加载/空数据/错误状态。查询失败时保留上次成功图表并标示状态。

## 真实数据接入

1. 管理员打开“系统管理 → 数据接入”，下载 CSV 模板。
2. 按 [数据契约](docs/DATA_CONTRACT.md) 从日志或现有数仓生成日事实。
3. 选择 CSV，确认导入方式后点击“开始导入”。
4. **首次真实导入替换全部演示用户、事实和投放费用**；后续按 `(user_id,date)` UPSERT，不删除未涉及记录。收藏/账号/规则保留。
5. 在同一页面录入渠道/日投放费用，或通过管理员 API 接入。

导入支持 UTF-8 / BOM，限制 10 MB 文件、12 MB HTTP 请求、50,000 行。非法数据返回行号；全部验证通过后一次事务提交，失败整体回滚。建议先备份数据库。

空库启动：

```bash
python server.py --empty --db data/production.db
```

`--empty` 只影响全新数据库。历史应完整回填，否则留存、累计实付、回流与 LTV 会受缺失影响。内部日期空洞按“未活跃”处理，接入方必须确保日批次完整。

## 权限与部署边界

| 角色 | 聚合指标 | 付费指标 | SQL / 分群明细 | 配置 / 导入 |
| --- | --- | --- | --- | --- |
| 管理员 | 是 | 是 | 是 | 是 |
| 数据分析师 | 是 | 是 | 是 | 否 |
| 运营 | 是 | 否（服务端置空，隐藏付费漏斗阶段） | 否 | 否 |
| 管理层 | 是 | 是 | 否 | 否 |

非管理员账号可限制一个渠道。服务端强制附加渠道条件，包含 SQL 与分群；前端筛选不能扩大权限。密码 PBKDF2-SHA256 保存；会话令牌存摘要，Cookie 为 HttpOnly / SameSite=Strict，8 小时过期；写接口验证 CSRF、Origin，登录有短期限速。SQL 在临时内存聚合库执行，不可读取用户表或文件。CSV 转义公式；分群标识为 SHA-256 摘要前 12 位，属于伪名化，不构成密码学匿名保证。

默认仅绑定 `127.0.0.1`，适用于本机验证。**没有声称达到 500 万 DAU、5 亿事件或 99.9% SLA**。标准库 HTTP 服务不应直接暴露公网；组织生产需网关/TLS、成熟 Web 服务、统一登录、备份、访问日志及压力测试。账户禁用/修改/自助找回尚未提供。

## 与完整方案的边界

Kafka / Flink / Spark、ClickHouse / Doris、Redis、向量库及调度平台未安装；存储与计算收敛到 SQLite。没有分钟级在线监控、采集 SDK、自动推送、后台日报、同比/目标预测、鲸鱼预警、自由事件漏斗、任意布尔分群、外部分群推送、多租户或 AI 模型能力。留存支持渠道/注册版本切片，尚无任意多组曲线叠加。

异常检测为日环比阈值，不是 STL / Isolation Forest。贡献拆解说明变化来源，不证明因果。SQL 针对当前过滤的聚合数据，不能访问裸表。漏斗基于累计阶段，非事件时间戳排序。分群使用固定 AND 条件。

## 配置与目录

```text
server.py                 HTTP API、鉴权、角色、SQL 沙箱、静态服务
data.py                   仓储、聚合、导入、演示数据生成
schema.sql                建表和索引
web/index.html            Vue 页面模板
web/app.js                状态、接口、图表组件和交互
web/styles.css            桌面/移动端样式
web/vendor/               Vue 3.5.13 / ECharts 5.6.0 与许可证
data/insight.db            首次启动生成（不随发行包分发）
tests/                    指标、权限与 HTTP 测试
docs/                     数据契约、AI 接口、架构、验收说明
start.cmd / start.sh       本地启动
Dockerfile / compose.yaml  可选容器配置
```

启动参数：`--host`、`--port`、`--db`、`--empty`、`--open`。前三项也支持 `HOST`、`PORT`、`DB_PATH` 环境变量。端口占用时可用 `python server.py --port 8767`。

备份：先停止服务，复制 `data/` 整个目录（含存在的 WAL/SHM 文件）；恢复时停止服务再替换。运行中备份应使用 SQLite Backup API，不能仅复制 `.db`。`.qa/` 测试数据库与正式数据隔离。

## 验证

```bash
python -m unittest discover -s tests -v
python -m py_compile server.py data.py
node --check web/app.js  # 可选，开发时需要 Node
```

测试使用临时数据库，不修改业务库。验收记录见 [QA.md](docs/QA.md)。

## 后续扩展

保持 `/api/analytics`、指标字典和权限契约，替换 `data.py` 查询可连接 ClickHouse / Doris。按 [AI_GATEWAY.md](docs/AI_GATEWAY.md) 添加服务端模型适配器，模型只能调用授权聚合工具。详见 [架构说明](docs/ARCHITECTURE.md)。

框架依据：[Vue 官方快速开始](https://vuejs.org/guide/quick-start)、[ECharts 尺寸与生命周期](https://echarts.apache.org/handbook/en/concepts/chart-size/)。依赖固定版本随项目分发，许可证与哈希位于 `web/vendor/`。
