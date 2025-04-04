import threading
import redis
import json
import os
from urllib.parse import urlparse


class SimpleCache:
    """Local in-memory cache."""

    def __init__(self):
        self.cache = {}

    def get(self, key):
        return self.cache.get(key)

    def set(self, key, value):
        existing_value = self.get(key)
        if isinstance(existing_value, dict) and isinstance(value, dict):
            existing_value.update(value)
        elif isinstance(existing_value, list):
            existing_value.append(value)
        else:
            self.cache[key] = value

    def delete(self, key):
        self.cache.pop(key, None)

    def clear(self):
        self.cache.clear()

    def size(self):
        return len(self.cache)


class RedisCache:
    """Redis-based cache using Azure Redis connection string."""

    def __init__(self, connection_string):
        self.client = self._connect_redis(connection_string)

    def _connect_redis(self, connection_string):
        """Parses the connection string and establishes a Redis connection."""
        parsed_url = urlparse(connection_string)
        host, port, password = (
            parsed_url[0],
            parsed_url[2][:4],
            parsed_url[2][14:].split(",")[0],
        )
        return redis.StrictRedis(
            host=host,
            port=port,
            password=password,
            ssl=True,  # Azure Redis uses SSL/TLS
            decode_responses=True,
        )

    def get(self, key):
        value = self.client.get(key)
        try:
            return json.loads(value) if value else None
        except json.JSONDecodeError:
            return value

    def set(self, key, value, ex=3600):
        """Set a value in Redis with an optional expiration time (in seconds)."""

        # if the key already exists, we need to merge the new value with the existing one
        existing_value = self.get(key)
        value_to_store = None
        if isinstance(existing_value, dict) and isinstance(value, dict):
            existing_value.update(value)
            value_to_store = json.dumps(existing_value)
        elif isinstance(existing_value, list):
            existing_value.append(value)
            value_to_store = json.dumps(existing_value)
        else:
            value_to_store = json.dumps(value)
        # expire time setting at 1hr considering the use case of phone call time
        self.client.set(key, value_to_store, ex=ex)

    def delete(self, key):
        self.client.delete(key)

    def size(self):
        return self.client.dbsize()


# Global cache instances
_cache = None
_lock = threading.Lock()


def get_cache():
    """Returns a singleton cache instance (Redis if available, otherwise in-memory)."""
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                redis_connection_string = os.getenv("AZURE_REDIS_CONNECTION_STRING")

                if (
                    redis_connection_string
                ):  # If Redis connection string is provided, use RedisCache
                    _cache = RedisCache(redis_connection_string)
                else:
                    _cache = (
                        SimpleCache()
                    )  # Fallback to in-memory cache use this for local testing only.
    return _cache


# testing the cache
if __name__ == "__main__":
    local_cache = get_cache()
    local_cache.set("key1", "value1")
    print(local_cache.get("key1"))  # Output: value1
    local_cache.set("key2", {"a": 1, "b": 2})
    print(local_cache.get("key2"))  # Output: {'a': 1, 'b': 2}
    local_cache.set("key2", {"c": 1, "d": 2})
    print(local_cache.get("key2"))  # Output: {'a': 1, 'b': 2, 'c': 1}
