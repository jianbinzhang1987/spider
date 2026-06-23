import os
import json
import time
import logging
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sessions", "exchange_rate_cache.json")
DEFAULT_RATE = 7.25  # Fallback exchange rate if API fails

def get_usd_to_cny_rate() -> float:
    """
    Get the live USD to CNY exchange rate.
    Uses a local file cache valid for 12 hours to avoid frequent external API requests.
    """
    # Create sessions directory if it doesn't exist
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    
    # Try reading from cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Cache validity: 12 hours (43200 seconds)
                if time.time() - data.get("timestamp", 0) < 43200:
                    rate = data.get("rate")
                    if rate and isinstance(rate, (int, float)):
                        logger.info(f"Using cached USD to CNY rate: {rate}")
                        return float(rate)
        except Exception as e:
            logger.warning(f"Failed to read exchange rate cache: {e}")

    # Fetch from API
    api_url = "https://open.er-api.com/v6/latest/USD"
    logger.info(f"Fetching live exchange rate from {api_url}...")
    try:
        response = httpx.get(api_url, timeout=10.0)
        if response.status_code == 200:
            result = response.json()
            if result.get("result") == "success":
                rate = result.get("rates", {}).get("CNY")
                if rate:
                    rate = float(rate)
                    logger.info(f"Successfully fetched exchange rate: {rate}")
                    # Save to cache
                    try:
                        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                            json.dump({"rate": rate, "timestamp": time.time()}, f, indent=4)
                    except Exception as ce:
                        logger.warning(f"Failed to write exchange rate cache: {ce}")
                    return rate
            logger.warning(f"API response result was not success: {result.get('result')}")
    except Exception as e:
        logger.error(f"Error fetching exchange rate: {e}. Falling back to default rate: {DEFAULT_RATE}")
    
    return DEFAULT_RATE

if __name__ == "__main__":
    rate = get_usd_to_cny_rate()
    print(f"USD to CNY Rate: {rate}")
