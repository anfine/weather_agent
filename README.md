# Weather Agent

一个支持当前天气和未来 7 天预报的 Python 天气 Agent，使用无需 API Key 的
[Open-Meteo](https://open-meteo.com/)。

## 运行

```bash
cd weather_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 main.py 北京
```

不传城市时默认查询上海：

```bash
python3 main.py
```

程序分两步请求：

1. 根据城市名查询经纬度。
2. 根据经纬度和可选海拔查询当前天气，或按日期范围查询每天汇总和小时级预报。

天气结果包含温度、体感温度、降水、湿度、紫外线、降雪、积雪、云量、
能见度和风等旅游评价所需字段。山峰等高差显著的景点可以显式传入天气
采样点的海拔，避免使用附近城区的默认海拔。

支持的问题示例：

```text
上海现在天气怎么样？
北京明天下午会下雨吗？
杭州周末哪天更适合出门？
未来十天成都天气怎么样？
```

V1 只提供未来 7 天预报。4～7 天的结果会提醒用户天气仍可能变化，超过
支持范围的日期不会被当成可靠预报。

## 景点天气评分

`scoring.py` 提供不依赖 LLM 的确定性评分功能。它读取
`data/attractions.json` 和 `data/scoring_rules.json`，按指定日期的小时天气
计算观景、徒步、户外游览三个体验分数，再根据景点中的重要性汇总综合分：

```python
from scoring import (
    evaluate_attraction_weather,
    load_attraction,
    load_scoring_rules,
)

attraction = load_attraction("华山")
rules = load_scoring_rules()
result = evaluate_attraction_weather(
    attraction=attraction,
    hourly=weather_payload["hourly"],
    target_date="2026-08-01",
    rules=rules,
)
```

默认评价时段为当天 `06:00～18:00`，结束时间不包含在评价区间内。

`main.py` 已将上述流程注册为 `evaluate_attraction` Agent 工具。用户询问
“明天适合去华山吗”时，工具会读取南峰坐标和海拔、查询当天小时天气、
执行确定性评分，再由模型解释结果。普通城市天气仍然使用
`find_city -> get_weather` 链路。

## 导入景点候选数据

`scripts/import_attractions.py` 读取 `data/54个景点.xlsx` 中的百度 BD-09 坐标，
保留原始值，离线近似转换为 WGS84，并通过 Open-Meteo Elevation API 批量
补全海拔：

```bash
python3 scripts/import_attractions.py
```

结果写入 `data/attractions_candidates.json`。城市和普通景点标记为自动数据；
山岳、峡谷、高原等地形敏感地点标记为 `needs_review`。华山和黄山使用已
核验的代表峰坐标与海拔覆盖原始粗略坐标。原始 Excel 不会被修改。

候选数据确认后，可生成 Agent 直接读取的单采样点景点库：

```bash
python3 scripts/build_runtime_attractions.py
```

V2 暂时不维护复杂景区的多海拔路线。地形敏感景点仍使用一个默认点，并在
Agent 回答中明确说明结果只是区域级参考。

## 城市级降级评价

景点库未命中时，`evaluate_attraction` 返回 `status=not_found`。Agent 可以
使用用户明确提供的城市，或者在地点著名且归属唯一时推断城市，再调用
`evaluate_city_outdoor`：

```text
灵隐寺未收录
→ LLM 推断杭州
→ find_city("杭州") 查询坐标和海拔
→ 查询杭州天气
→ 只计算 outdoor_visit 通用户外分
```

模型只允许推断城市名称，不允许生成经纬度或海拔。名称有歧义时，Agent
应询问用户具体城市。降级结果始终标记为 `coverage=city_fallback`，并说明
结果不代表景点局部天气。

命令行只在这种未解决的城市歧义出现时保留最小进程内上下文。例如：

```text
$ python3 main.py "明天适合去爬老君山吗？"
请问是河南洛阳还是云南丽江的老君山？

你> 河南洛阳的
```

第二轮会继承上一轮的“老君山”和目标日期，完成城市级评价。上下文仅保留
在本次 Python 进程内，程序退出后不会写入数据库或持久化会话。

## Flask API

设置模型密钥后启动 Web 服务：

```bash
export DEEPSEEK_API_KEY="你的密钥"
python3 app.py
```

默认监听 `127.0.0.1:5000`。可以用 `HOST` 和 `PORT` 环境变量修改监听地址。

发送一轮对话：

```bash
curl -X POST http://127.0.0.1:5000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"明天适合去华山吗？"}'
```

响应中的 `session_id` 用于后续追问：

```json
{
  "session_id": "c951fc4e...",
  "reply": "……",
  "needs_follow_up": false
}
```

下一轮继续传入相同的 `session_id`：

```bash
curl -X POST http://127.0.0.1:5000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"c951fc4e...","message":"河南洛阳的"}'
```

健康检查为 `GET /api/health`，清除会话为
`DELETE /api/sessions/<session_id>`。MVP 会话保存在 Flask 进程内存中，服务
重启即清空；接入 MySQL 后可将会话存储替换为持久化实现。
