from db.redis import get_redis
from db.repositories.collect_repository import CollectRepository
from db.session import AsyncSessionLocal
from consumer.handler import make_error_handler, make_inventory_handler, make_metrics_handler

_redis = get_redis()

inventory_handler = make_inventory_handler(AsyncSessionLocal, CollectRepository, _redis)
metrics_handler   = make_metrics_handler(AsyncSessionLocal, CollectRepository, _redis)
error_handler     = make_error_handler(_redis)