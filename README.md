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

## 导入全国景区数据

`scripts/import_attractions.py` 读取 `data/01-23年全国景区数据.xlsx` 的 14 个
业务列，直接使用表中的 WGS84 坐标，并根据“省份、名称、地址”生成稳定 ID。
导入前可先执行只读检查：

```bash
.venv/bin/python scripts/import_attractions.py --dry-run
```

确认统计结果后执行幂等同步和数据库校验：

```bash
.venv/bin/python scripts/import_attractions.py
.venv/bin/python scripts/check_attractions.py
```

同一业务键的重复记录会合并；等级冲突优先采用评定时间更新的记录并输出
冲突报告。尚未完成 LLM 活动分类的全国景区标记为 `pending`，暂时使用
`outdoor_visit` 通用户外规则。`data/attractions.json` 继续保留精选景点、别名
和评分基线；种子导入后再运行全国导入器，可保留这些精选数据。

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

## Docker Compose 部署

应用、MySQL 和 Redis 统一由 Docker Compose 管理：

```text
weather.anfine.top
  → Caddy（HTTPS）
  → 127.0.0.1:8000
  → app 容器：Gunicorn（1 worker、4 threads）+ Flask
       ├── mysql:3306
       └── redis:6379
```

`app` 使用非 root 用户运行。MySQL、Redis 和 app 的宿主机端口只绑定到
`127.0.0.1`，不会直接暴露到公网；容器之间通过 Compose 服务名通信。

### 准备环境

服务器需要安装 Git、Docker Engine 和 Docker Compose 插件，不需要在宿主机创建
Python 虚拟环境。下面假设代码位于 `/opt/weather-agent`：

```bash
sudo git clone \
  https://github.com/anfine/weather_agent.git \
  /opt/weather-agent

cd /opt/weather-agent
sudo cp .env.example .env
sudo chmod 600 .env
sudoedit .env
```

填写 `DEEPSEEK_API_KEY`、`MYSQL_PASSWORD` 和 `MYSQL_ROOT_PASSWORD`。建议使用
URL 安全的随机密码，避免数据库连接字符串的转义问题：

```bash
openssl rand -hex 24
```

`.env` 只在容器启动时注入，不会进入应用镜像，也不能提交到 Git。Compose 会在
app 容器内把数据库和 Redis 地址覆盖为 `mysql:3306` 与 `redis:6379`。

修改配置后先做只读检查。不要分享完整的 `docker compose config` 输出，因为它会
展开密钥：

```bash
sudo docker compose config --quiet
sudo docker compose config --services
```

### 首次部署

先构建应用镜像并启动数据服务：

```bash
sudo docker compose build app
sudo docker compose up -d mysql redis
sudo docker compose ps
```

等待 MySQL 和 Redis 均变为 `healthy`，再用同一个 app 镜像运行一次性数据库任务：

```bash
sudo docker compose run --rm app alembic upgrade head
sudo docker compose run --rm app python scripts/seed_attractions.py
sudo docker compose run --rm app python scripts/import_attractions.py
sudo docker compose run --rm app python scripts/check_attractions.py
```

执行顺序不能交换：Alembic 先创建当前表结构，精选种子提供人工标签、别名和气象点，
全国导入再合并批量数据，最后的检查脚本验证14,840个全国业务键和14,893条最终景点。
种子和全国导入均为幂等操作，可以安全重试；检查失败会返回非零退出码。

数据通过验收后再启动 Web 服务：

```bash
sudo docker compose up -d app
sudo docker compose ps
```

app 健康检查使用 `/api/ready`，只有 Gunicorn 能响应、MySQL 可连接且 Alembic 已达到
`head` 时才会变为 `healthy`。迁移和导入不会藏在 Gunicorn 启动命令中，因此多个
worker 或容器启动时不会重复写数据库。

### 本机冒烟测试

```bash
curl --fail http://127.0.0.1:8000/
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/api/ready
```

两个检查接口应分别返回 `{"status":"ok"}` 和 `{"status":"ready"}`。不调用 LLM
也可以在 app 容器内验证正式名称与别名查询：

```bash
sudo docker compose exec app python -c \
"from repositories.attraction import load_attraction; a = load_attraction('华山'); b = load_attraction('西岳'); print({'name': a['name'], 'same_id': a['id'] == b['id']})"
```

预期输出 `{'name': '华山', 'same_id': True}`。随后在网页完成一次真实对话，确认天气
查询、评分和会话链路可用；聊天接口按 IP 限流，冒烟测试不要连续发送过多问题。

### 配置 Caddy 和 DNS

为 `weather.anfine.top` 添加指向服务器公网 IPv4 的 DNS `A` 记录。没有正确配置
公网 IPv6 时不要添加 `AAAA` 记录。若使用 Cloudflare，V1 建议先使用“仅 DNS”。

`deploy/Caddyfile` 将公网 HTTPS 请求转发到只监听宿主机回环地址的 app 端口：

```caddyfile
weather.anfine.top {
    reverse_proxy 127.0.0.1:8000
}
```

修改现有 Caddy 配置前先备份，验证成功后只 reload：

```bash
sudo cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-weather
sudoedit /etc/caddy/Caddyfile

sudo -u caddy /usr/bin/caddy validate \
  --config /etc/caddy/Caddyfile \
  --adapter caddyfile

sudo systemctl reload caddy
```

线上检查：

```bash
curl --fail https://weather.anfine.top/
curl --fail https://weather.anfine.top/api/health
curl --fail https://weather.anfine.top/api/ready
```

### 更新版本

应用更新时先拉取代码并构建新镜像：

```bash
cd /opt/weather-agent
sudo git pull --ff-only
sudo docker compose build app
```

停止 Web 服务后执行数据库任务，避免导入和清缓存期间仍有旧请求写回缓存：

```bash
sudo docker compose stop app
sudo docker compose up -d mysql redis
sudo docker compose run --rm app alembic upgrade head
sudo docker compose run --rm app python scripts/seed_attractions.py
sudo docker compose run --rm app python scripts/import_attractions.py
sudo docker compose run --rm app python scripts/check_attractions.py
sudo docker compose up -d app
```

最后确认容器和就绪状态：

```bash
sudo docker compose ps
curl --fail http://127.0.0.1:8000/api/ready
```

自动部署脚本应启用 `set -e`，让迁移、导入或检查的非零退出码立即中止发布。不要把
`alembic upgrade` 或数据导入放进 Dockerfile，也不要放进每个 Gunicorn worker 的
启动流程。

### 日志和数据卷

```bash
sudo docker compose logs -f app
sudo docker compose logs --tail=100 mysql
sudo docker compose logs --tail=100 redis
```

MySQL 数据保存在 Compose 命名卷 `mysql_data` 中。普通的镜像重建、容器重建和
`docker compose down` 不会删除数据；`docker compose down --volumes` 会永久删除
数据库卷，只能用于明确创建的临时验收环境。
