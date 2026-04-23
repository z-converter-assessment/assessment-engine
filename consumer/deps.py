from db.repositories.collect_repository import CollectRepository
from db.session import AsyncSessionLocal
from consumer.handler import make_handler

handler = make_handler(AsyncSessionLocal, CollectRepository)