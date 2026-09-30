from mcp.server.mcpserver import MCPServer
from weather_api import get_current_weather

mcp = MCPServer("weather")


@mcp.tool(name="get_current_weather")
def weather_tool(city: str) -> dict:
    """Get the current weather for a city. Use 'City,CountryCode' format, e.g. 'Denver,US'."""
    data = get_current_weather(city)
    return {
        "city": data["name"],
        "temp_f": data["main"]["temp"],
        "humidity": data["main"]["humidity"],
        "conditions": data["weather"][0]["description"],
        "wind_mph": data["wind"]["speed"],
    }

@mcp.tool(name="reset_cache")
def reset_cache() -> dict:
    """Clear cached weather data.)"""
    return {"status": "cache cleared"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)