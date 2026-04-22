from db.repositories.collect_repository import CollectRepository
from db.session import AsyncSessionLocal
from consumer.consumer import make_handler

handler = make_handler(AsyncSessionLocal, CollectRepository)