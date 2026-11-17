import logging, time
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models.entities import Recovery
from src.services.recovery_runner import execute
redis = Redis.from_url(REDIS_URL)
logging.basicConfig(level=logging.INFO)
# Redis leadership lock ensures only one worker processes the durable DB queue.
while True:
    Path('/tmp/heartbeat').touch()
    lock = redis.lock('wal-worker', timeout=15, blocking_timeout=1)
    try:
        with lock:
            with Session() as db:
                # A crashed worker leaves jobs in running; the lock guarantees no
                # executor is active, so requeue them to continue from persisted progress.
                for stale in db.scalars(select(Recovery).where(Recovery.status == 'running')).all():
                    stale.status = 'queued'
                db.commit()
                job = db.scalar(select(Recovery).where(Recovery.status == 'queued').order_by(Recovery.id).with_for_update(skip_locked=True))
                if job:
                    job.status = 'running'
                    db.commit()
                    try:
                        execute(job, db)
                    except ValueError as exc:
                        job.result = {'error': str(exc), 'simulation': True}
                        job.status = 'failed'
                        db.commit()
                    logging.info('recovery %s %s', job.id, job.status)
    except Exception:
        logging.exception('worker retry')
    time.sleep(1)
