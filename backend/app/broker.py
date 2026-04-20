from taskiq_aio_pika import AioPikaBroker

from app.config import settings

broker = AioPikaBroker(settings.broker_url)