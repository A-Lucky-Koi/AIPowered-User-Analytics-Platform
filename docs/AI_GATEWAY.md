# AI Gateway 预留契约

## 当前状态

AI **未接入**。没有模型 SDK、API Key、外部调用、预测计算或伪造回复。界面包含分析中心、侧边助手、图表解读入口及 SQL 辅助入口。没有“填一个 API Key 即可启用”的承诺；接入需实现服务端适配器。

- `GET /api/ai/status` → `200 {enabled:false, provider:null, message:"..."}`。
- `POST /api/ai/chat`、`/api/ai/interpret`、`/api/ai/forecast` → `501 {enabled:false, code:"AI_NOT_CONFIGURED", error:"..."}`。
- 以上数据接口已经过统一登录鉴权；POST 校验 CSRF、Origin，并写入 `ai_disabled` 审计，不保存当前问题正文。

前端问题建议、规则摘要和贡献拆解是确定性 UI/计算，明确与模型能力区分。

## 接入后的推荐接口（设计契约，尚未实现）

```json
{
  "message": "上周安卓渠道的7日留存对比",
  "conversation_id": "server-issued-id",
  "filters": {
    "start": "2026-10-26", "end": "2026-11-01",
    "channel": "all", "os": "Android",
    "server": "all", "version": "all"
  }
}
```

用户身份、角色和渠道权限仅从服务端会话取得，不能信任请求中宣称的 user_id/role/scope。conversation_id 必须校验拥有者。secret 只存服务端，不通过前端保存到 localStorage。

建议 SSE 事件：`status`、`metric_result`、`text_delta`、`done`、`error`。返回的每个数字应引用 metric_result 的查询结果，带 source/query_id/filters/data_updated_at；无法解析的指标或时间应要求用户澄清。

## 受控工具

```text
query_metric(metric, dimensions, filters, range)
drill_down(metric, dimension, filters, range)
compare(metric, groups, range)
run_sql(sql, filters)              # 复用聚合沙箱，不能得到裸表连接
detect_anomaly(metric, filters)    # 清楚声明检测方法
forecast(metric, horizon)          # 实际实现模型后才开放
```

所有工具必须通过 `server.require` 和 `data.scope` 的等效权限验证。不要让模型拼接行级权限条件；权限应由仓储层强制注入。对运营角色不得给付费结果、付费漏斗或分群明细。发送外部模型时仅限获准的聚合结果和字典，不包括 user_id、设备明细、密码、令牌。

## 建议系统提示词

```text
你是游戏数据分析助手。仅依据工具返回的聚合数据与指标字典回答。
所有数值必须能追溯到查询结果，标注日期、过滤条件、口径和数据来源。
未成熟留存、缺失数据、分母为零不能当作0；不要跨角色请求或透露数据。
把观察、算术分解、假设和因果结论分开。无证据时明确需要验证。
不能执行数据修改、任意代码或绕过查询限制。问题有歧义先澄清。
模型/工具不可用时返回真实状态，不编造图表或预测。
```

## 接入验收

需要覆盖：多轮上下文归属、低置信度澄清、权限旁路、恶意提示、SQL 超时、模型断流、服务取消、费用/并发限制、数字与结果一致性、审计与个人数据过滤。预测必须包含真实模型方法、训练时间窗和置信区间，不能仅画延长线。接入完成前保持 `enabled:false`。
