import argparse
import ast
import json
import os
import sys
from datetime import date, timedelta

import requests
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.messages import AIMessage, ToolMessage
from langchain.tools import tool
from langchain_openai import ChatOpenAI

from redis_client import redis_client
from repositories.attraction import AmbiguousAttractionError
from scoring import evaluate_attraction_weather, load_attraction
from weather_cache import (
    WeatherCache,
    get_current_with_cache,
    get_forecast_with_cache,
)


load_dotenv()


GEOCODING_API = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"
CURRENT_WEATHER_CACHE_TTL_SECONDS = int(
    os.getenv("CURRENT_WEATHER_CACHE_TTL_SECONDS", "300")
)
FORECAST_WEATHER_CACHE_TTL_SECONDS = int(
    os.getenv("FORECAST_WEATHER_CACHE_TTL_SECONDS", "1800")
)

CURRENT_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "snowfall",
    "snow_depth",
    "weather_code",
    "uv_index",
    "wind_speed_10m",
]

HOURLY_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation_probability",
    "precipitation",
    "snowfall",
    "snow_depth",
    "weather_code",
    "cloud_cover",
    "visibility",
    "uv_index",
    "wind_speed_10m",
    "wind_gusts_10m",
]

DAILY_FIELDS = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_sum",
    "precipitation_probability_max",
    "snowfall_sum",
    "uv_index_max",
    "relative_humidity_2m_mean",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "sunrise",
    "sunset",
]

weather_cache = WeatherCache(redis_client)

model = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com"
)


@tool
def find_city(city: str) -> dict:
    """根据城市名查询经纬度。"""
    response = requests.get(
        GEOCODING_API,
        params={"name": city, "count": 1, "language": "zh", "format": "json"},
        timeout=10,
    )
    response.raise_for_status()

    results = response.json().get("results", [])
    if not results:
        raise ValueError(f"找不到城市：{city}")
    return results[0]


def _parse_forecast_range(start_date: str, end_date: str) -> tuple[date, date]:
    """校验 V1 支持的未来 7 天预报范围。"""
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as error:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式") from error

    today = date.today()
    last_supported_date = today + timedelta(days=7)

    if start > end:
        raise ValueError("start_date 不能晚于 end_date")
    if start < today:
        raise ValueError("V1 不支持查询历史天气")
    if end > last_supported_date:
        raise ValueError(
            f"V1 只支持未来 7 天，最晚可查询到 {last_supported_date.isoformat()}"
        )
    if (end - start).days >= 7:
        raise ValueError("单次查询最多包含 7 个自然日")

    return start, end


def _request_weather(
    params: dict[str, object],
) -> dict:
    """调用 Open-Meteo 并返回原始天气 payload。"""
    response = requests.get(
        WEATHER_API,
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


@tool
def get_weather(
    latitude: float,
    longitude: float,
    elevation: float | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """查询当前天气或未来 7 天预报。

    elevation 是可选的海拔高度（米）；查询山峰等高差显著的景点时应传入。
    查询“现在”时不要传 start_date 和 end_date。查询某天或日期范围时，
    两个日期都必须传入 YYYY-MM-DD；返回 daily 汇总和 hourly 小时数据。
    """
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date 和 end_date 必须同时提供")

    params: dict[str, object] = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "auto",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }
    if elevation is not None:
        params["elevation"] = elevation

    is_forecast = start_date is not None and end_date is not None
    if is_forecast:
        start, end = _parse_forecast_range(start_date, end_date)
        params.update(
            {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hourly": ",".join(HOURLY_FIELDS),
                "daily": ",".join(DAILY_FIELDS),
            }
        )
    else:
        params["current"] = ",".join(CURRENT_FIELDS)

    if not is_forecast:
        payload = get_current_with_cache(
            weather_cache,
            latitude=latitude,
            longitude=longitude,
            elevation=elevation,
            fetch_current=lambda: _request_weather(params),
            ttl_seconds=CURRENT_WEATHER_CACHE_TTL_SECONDS,
        )
        return {
            "timezone": payload.get("timezone"),
            "timezone_abbreviation": payload.get("timezone_abbreviation"),
            "utc_offset_seconds": payload.get("utc_offset_seconds"),
            "current_units": payload.get("current_units"),
            "current": payload["current"],
        }

    def fetch_forecast(fetch_start: date, fetch_end: date) -> dict:
        request_params = {
            **params,
            "start_date": fetch_start.isoformat(),
            "end_date": fetch_end.isoformat(),
        }
        return _request_weather(request_params)

    payload = get_forecast_with_cache(
        weather_cache,
        latitude=latitude,
        longitude=longitude,
        elevation=elevation,
        start_date=start,
        end_date=end,
        fetch_forecast=fetch_forecast,
        ttl_seconds=FORECAST_WEATHER_CACHE_TTL_SECONDS,
    )

    result = {
        "timezone": payload.get("timezone"),
        "timezone_abbreviation": payload.get("timezone_abbreviation"),
        "utc_offset_seconds": payload.get("utc_offset_seconds"),
        "hourly_units": payload.get("hourly_units"),
        "hourly": payload["hourly"],
        "daily_units": payload.get("daily_units"),
        "daily": payload["daily"],
    }
    if (end - date.today()).days >= 4:
        result["forecast_notice"] = "4～7 天预报可能变化，请临近出行时再次确认。"
    return result


@tool
def evaluate_attraction(
    attraction_name: str,
    target_date: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    """评价指定日期去某个景点游览的天气适宜度。

    attraction_name 可以是景点、城市、地区名称或别名，target_date 必须使用
    YYYY-MM-DD。
    start_time 和 end_time 可选，使用 HH:MM；不传时按默认日间时段评分。
    返回确定性计算的综合分、体验分、指标明细及主要影响因素。
    """
    try:
        attraction = load_attraction(attraction_name)
    except AmbiguousAttractionError as error:
        return {
            "status": "ambiguous",
            "requested_place": attraction_name,
            "candidates": error.candidates,
            "notice": "存在多个同名景点，请补充省、市或区县。",
        }
    except ValueError:
        return {
            "status": "not_found",
            "requested_place": attraction_name,
            "notice": "地点尚未收录，可在确认所在城市后使用城市级户外评价。",
        }
    default_point_id = attraction["default_weather_point_id"]
    weather_point = next(
        (
            point
            for point in attraction.get("weather_points", [])
            if point.get("id") == default_point_id
        ),
        None,
    )
    if weather_point is None:
        raise ValueError(
            f"景点 {attraction['name']} 缺少默认天气采样点：{default_point_id}"
        )

    weather = get_weather.invoke(
        {
            "latitude": weather_point["latitude"],
            "longitude": weather_point["longitude"],
            "elevation": weather_point.get("elevation_m"),
            "start_date": target_date,
            "end_date": target_date,
        }
    )
    evaluation = evaluate_attraction_weather(
        attraction=attraction,
        hourly=weather["hourly"],
        target_date=target_date,
        start_time=start_time,
        end_time=end_time,
    )
    evaluation["weather_point"] = {
        "id": weather_point["id"],
        "name": weather_point["name"],
        "latitude": weather_point["latitude"],
        "longitude": weather_point["longitude"],
        "elevation_m": weather_point.get("elevation_m"),
    }
    evaluation["coverage"] = attraction.get(
        "coverage",
        "representative_point",
    )
    if attraction.get("weather_notice"):
        evaluation["weather_notice"] = attraction["weather_notice"]
    if "forecast_notice" in weather:
        evaluation["forecast_notice"] = weather["forecast_notice"]
    evaluation["status"] = "ok"
    return evaluation


@tool
def evaluate_city_outdoor(
    city_name: str,
    target_date: str,
    requested_place: str | None = None,
    city_resolution: str = "user_provided",
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    """使用城市天气对未收录地点做通用户外游览评价。

    city_name 只能传城市名称，不要传经纬度或海拔。requested_place 是用户原本
    询问的未收录地点。city_resolution 只能是 user_provided 或 llm_inferred，
    分别表示城市由用户明确提供或由模型根据无歧义的常识推断。
    """
    allowed_resolutions = {"user_provided", "llm_inferred"}
    if city_resolution not in allowed_resolutions:
        raise ValueError(
            "city_resolution 必须是 user_provided 或 llm_inferred"
        )

    city = find_city.invoke({"city": city_name})
    weather = get_weather.invoke(
        {
            "latitude": city["latitude"],
            "longitude": city["longitude"],
            "elevation": city.get("elevation"),
            "start_date": target_date,
            "end_date": target_date,
        }
    )
    resolved_city = city.get("name") or city_name
    display_name = requested_place or resolved_city
    synthetic_attraction = {
        "id": f"city-fallback-{resolved_city}",
        "name": display_name,
        "experience_tags": [
            {
                "id": "outdoor_visit",
                "importance": 1,
            }
        ],
    }
    evaluation = evaluate_attraction_weather(
        attraction=synthetic_attraction,
        hourly=weather["hourly"],
        target_date=target_date,
        start_time=start_time,
        end_time=end_time,
    )
    evaluation.update(
        {
            "status": "ok",
            "coverage": "city_fallback",
            "requested_place": requested_place,
            "resolved_city": resolved_city,
            "city_resolution": city_resolution,
            "weather_point": {
                "name": resolved_city,
                "latitude": city["latitude"],
                "longitude": city["longitude"],
                "elevation_m": city.get("elevation"),
                "timezone": city.get("timezone"),
            },
            "weather_notice": (
                f"{display_name}尚未收录，结果基于{resolved_city}城市天气和"
                "通用户外游览规则，不代表景点局部条件。"
            ),
        }
    )
    if "forecast_notice" in weather:
        evaluation["forecast_notice"] = weather["forecast_notice"]
    return evaluation


AGENT_SYSTEM_PROMPT = (
        "你是天气与景点游览助手。"
        "用户询问普通城市天气时，必须先调用 find_city 获取经纬度，"
        "再调用 get_weather。"
        "如果 find_city 返回 elevation，调用 get_weather 时一并传入；"
        "用户提到景点、旅游城市或旅游地区并询问‘今天怎么样’，或者询问是否适合游览、"
        "观景或徒步时，先调用 evaluate_attraction，不要先调用 find_city，"
        "也不要自行计算分数。"
        "如果 evaluate_attraction 返回 status=not_found：用户已明确说出城市时，"
        "调用 evaluate_city_outdoor，并把 city_resolution 设为 user_provided；"
        "如果用户没说城市，但地点非常著名且所在城市唯一明确，可以推断城市后调用，"
        "并把 city_resolution 设为 llm_inferred；如果名称有歧义或不确定，"
        "不要猜测，直接询问用户所在城市。"
        "当用户在下一条消息中补充城市或具体地点时，必须结合上一轮保留的"
        "requested_place 和 target_date 继续调用 evaluate_city_outdoor，"
        "不要要求用户重复景点名称和日期。"
        "绝对不要由模型生成经纬度或海拔；evaluate_city_outdoor 内部会查询这些数据。"
        "评分和影响因素必须忠实依据评价工具的返回结果；"
        "不得修改工具给出的分数。"
        "如果工具返回 weather_notice，回答中必须简要说明其精度限制。"
        f"今天是 {date.today().isoformat()}。"
        "用户问“现在”时不传日期；问某天、上午、下午、晚上或日期范围时，"
        "先把时间表达转换为 YYYY-MM-DD，再同时传 start_date 和 end_date。"
        "上午按 06:00～11:59、下午按 12:00～17:59、晚上按 18:00～23:59，"
        "从 hourly 数据中筛选对应小时。"
        "V1 只提供未来 7 天；用户询问更远日期时，清楚说明范围限制，"
        "可以回答支持范围内的部分，但不要把远期数据描述成可靠预报。"
        "比较出行日期时结合降水概率、降水量、天气代码、气温和风速。"
        "找不到地点时，应清楚说明尚未收录，并请用户提供所在城市以便查询普通天气。"
        "当已经获得天气或评分结果、足以回答用户问题时，给出结论后直接结束；"
        "不要用问题、邀请继续查询或‘需要我再帮你……吗’之类的话收尾。"
        "只有缺少城市等必要信息、当前问题确实无法继续处理时，才提出一个简短的澄清问题。"
)


agent = create_agent(
    model=model,
    tools=[
        find_city,
        get_weather,
        evaluate_attraction,
        evaluate_city_outdoor,
    ],
    system_prompt=AGENT_SYSTEM_PROMPT,
)


def _tool_payload(message: ToolMessage) -> dict:
    """尽量解析工具消息中的结构化返回值。"""
    content = message.content
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(content)
        except (SyntaxError, ValueError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


def needs_city_follow_up(messages: list) -> bool:
    """判断最近一次景点评价是否停在待确认城市的状态。"""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        if message.name == "evaluate_city_outdoor":
            return False
        if message.name == "evaluate_attraction":
            return _tool_payload(message).get("status") in {
                "not_found",
                "ambiguous",
            }
    return False


def invoke_agent_turn(messages: list, query: str) -> dict:
    """在已有进程内对话消息上追加一轮用户输入。"""
    return agent.invoke(
        {
            "messages": [
                *messages,
                {
                    "role": "user",
                    "content": query,
                },
            ]
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="查询城市当前天气和未来 7 天预报")
    parser.add_argument(
        "query",
        nargs="?",
        default="上海今天天气怎么样？",
        help="天气问题",
    )

    args = parser.parse_args()

    messages: list = []
    query = args.query

    while True:
        try:
            result = invoke_agent_turn(messages, query)
        except Exception as error:
            raise SystemExit(f"查询失败：{error}") from error

        messages = result["messages"]
        print(messages[-1].content)

        if not needs_city_follow_up(messages) or not sys.stdin.isatty():
            break

        try:
            query = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not query or query.casefold() in {"exit", "quit", "退出"}:
            break

if __name__ == "__main__":
    main()
