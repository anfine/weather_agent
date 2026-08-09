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

`scoring.py` 提供不依赖 LLM 的确定性评分功能。景点、别名、天气采样点和体验标签
通过 SQLAlchemy 从 MySQL 读取，评分规则保留在 `data/scoring_rules.json`，再按指定
日期的小时天气计算观景、徒步、户外游览三个体验分数：

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

`GET /api/health` 只表示 Flask 进程存活；`GET /api/ready` 还会检查 MySQL 连接和
Alembic revision 是否达到 `head`。清除会话使用
`DELETE /api/sessions/<session_id>`。

V1 会话保存在 Flask 进程内存中，服务重启即清空。服务最多保存 200 个会话，每个
会话保留最近 6 轮，空闲 30 分钟后过期；匿名聊天接口按客户端 IP 限制为 3 小时
10 次请求。

## Ubuntu 生产部署

线上使用以下链路：

```text
weather.anfine.top
  → Caddy（HTTPS）
  → 127.0.0.1:8000
  → Gunicorn（1 worker、4 threads）
  → Flask
  → Docker MySQL
```

Flask 开发服务器仅用于本地调试，公网环境必须使用 Gunicorn。下面假设代码位于
`/opt/weather-agent`，服务使用无登录权限的 `weather-agent` 用户运行。

### 1. 创建用户并获取代码

```bash
sudo useradd \
  --system \
  --create-home \
  --home-dir /var/lib/weather-agent \
  --shell /usr/sbin/nologin \
  weather-agent

sudo install -d \
  -o weather-agent \
  -g weather-agent \
  -m 0755 \
  /opt/weather-agent

sudo -u weather-agent git clone \
  https://github.com/anfine/weather_agent.git \
  /opt/weather-agent
```

私有仓库需要先为服务器配置只读 Deploy Key。

### 2. 安装 Python 依赖

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip

sudo -u weather-agent \
  python3 -m venv /opt/weather-agent/.venv

sudo -u weather-agent \
  /opt/weather-agent/.venv/bin/pip install \
  -r /opt/weather-agent/requirements.txt
```

### 3. 配置生产环境变量

真实密钥只保存在服务器，不提交 Git：

```bash
sudo install -d \
  -o root \
  -g weather-agent \
  -m 0750 \
  /etc/weather-agent

sudo install \
  -o root \
  -g weather-agent \
  -m 0640 \
  /opt/weather-agent/deploy/weather-agent.env.example \
  /etc/weather-agent/weather-agent.env

sudoedit /etc/weather-agent/weather-agent.env
```

填写 `DEEPSEEK_API_KEY`、MySQL 应用密码和 root 密码，并保证 `DATABASE_URL` 中的
应用密码与 `MYSQL_PASSWORD` 相同。建议使用十六进制随机密码，避免 URL 编码问题：

```bash
openssl rand -hex 24
```

### 4. 启动 MySQL

MySQL 由 Docker Compose 管理，并且只映射到宿主机回环地址：

```bash
sudo docker compose \
  --env-file /etc/weather-agent/weather-agent.env \
  -f /opt/weather-agent/docker-compose.yml \
  up -d mysql

sudo docker compose \
  --env-file /etc/weather-agent/weather-agent.env \
  -f /opt/weather-agent/docker-compose.yml \
  ps
```

等待 MySQL 状态变为 `healthy`。

### 5. 执行迁移、种子导入和检查

```bash
sudo -u weather-agent bash -c '
  set -a
  source /etc/weather-agent/weather-agent.env
  set +a
  cd /opt/weather-agent

  .venv/bin/alembic upgrade head
  .venv/bin/python scripts/seed_attractions.py
  .venv/bin/python scripts/check_attractions.py
  .venv/bin/python -c "
from database import check_database_readiness
check_database_readiness()
print(\"database ready\")
"
'
```

种子脚本是幂等的，重复执行不会产生重复景点。

### 6. 安装并启动 Gunicorn 服务

```bash
sudo install \
  -o root \
  -g root \
  -m 0644 \
  /opt/weather-agent/deploy/weather-agent.service \
  /etc/systemd/system/weather-agent.service

sudo systemctl daemon-reload
sudo systemctl enable weather-agent
sudo systemctl start weather-agent

sudo systemctl status weather-agent --no-pager
sudo journalctl -u weather-agent -n 100 --no-pager
```

先在服务器本机验证 Gunicorn：

```bash
curl --fail http://127.0.0.1:8000/
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/api/ready
```

预期两个检查接口分别返回 `{"status":"ok"}` 和 `{"status":"ready"}`。

### 7. 配置 Caddy 和 DNS

为 `weather.anfine.top` 添加指向服务器公网 IPv4 的 DNS `A` 记录。没有正确配置
公网 IPv6 时不要添加 `AAAA` 记录。若使用 Cloudflare，V1 建议先使用“仅 DNS”。

`deploy/Caddyfile` 保留现有 `anfine.top` 代理，并为 Weather Agent 添加独立站点：

```caddyfile
anfine.top {
    reverse_proxy 127.0.0.1:8080
}

weather.anfine.top {
    reverse_proxy 127.0.0.1:8000
}
```

修改服务器配置前先备份，验证成功后只 reload，不 restart：

```bash
sudo cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-weather
sudoedit /etc/caddy/Caddyfile

sudo -u caddy /usr/bin/caddy validate \
  --config /etc/caddy/Caddyfile \
  --adapter caddyfile

sudo systemctl reload caddy
```

### 8. 线上冒烟测试

```bash
curl -I https://anfine.top/
curl -I https://weather.anfine.top/
curl --fail https://weather.anfine.top/api/health
curl --fail https://weather.anfine.top/api/ready
```

随后在网页验证华山、别名“西岳”、“老君山 → 河南洛阳”的最小追问、清空会话和
错误提示。聊天接口按 IP 限流，冒烟测试不要连续发送过多问题。

### 9. 更新版本

```bash
sudo -u weather-agent git -C /opt/weather-agent pull --ff-only
sudo -u weather-agent \
  /opt/weather-agent/.venv/bin/pip install \
  -r /opt/weather-agent/requirements.txt
```

更新数据库并重启应用：

```bash
sudo -u weather-agent bash -c '
  set -a
  source /etc/weather-agent/weather-agent.env
  set +a
  cd /opt/weather-agent
  .venv/bin/alembic upgrade head
  .venv/bin/python scripts/seed_attractions.py
'

sudo systemctl restart weather-agent
curl --fail http://127.0.0.1:8000/api/ready
```

Caddyfile 没有变化时不需要 reload Caddy。应用日志通过以下命令查看：

```bash
sudo journalctl -u weather-agent -f
sudo journalctl -u caddy -f
```
