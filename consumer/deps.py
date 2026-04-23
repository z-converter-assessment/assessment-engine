from db.repositories.collect_repository import CollectRepository
from db.session import AsyncSessionLocal
from consumer.handler import make_error_handler, make_inventory_handler, make_metrics_handler

inventory_handler = make_inventory_handler(AsyncSessionLocal, CollectRepository)
metrics_handler   = make_metrics_handler(AsyncSessionLocal, CollectRepository)
error_handler     = make_error_handler()
