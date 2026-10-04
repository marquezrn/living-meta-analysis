"""Production queue; monitoring never dispatches extraction."""

from celery import Celery

from .config import get_settings

settings = get_settings()
celery_app = Celery("livingmeta", broker=settings.broker_url)
celery_app.conf.update(task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, task_track_started=True,
    broker_transport_options={"visibility_timeout": 21600}, task_serializer="json", accept_content=["json"],
    result_serializer="json", task_soft_time_limit=21000, task_time_limit=21600)


@celery_app.task(bind=True, name="livingmeta.execute_run", max_retries=0)
def execute_run(self, run_id, recovery=False):
    from .db import make_database
    from .worker import process_run
    engine, sessions = make_database(settings)
    try:
        process_run(run_id, sessions, settings,
                    recovery=recovery or bool(self.request.delivery_info.get("redelivered")))
    finally:
        engine.dispose()
