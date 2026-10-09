"""Explicit queue protocol fake for pre-existing worker lifecycle tests."""
from uuid import uuid4
from services.job_queue import Job


class MemoryQueue:
    def __init__(self, db):
        self.db = db

    async def enqueue(self, kind, interval):
        return 'queued'

    async def claim(self, kind, worker_id):
        return Job(str(uuid4()), str(uuid4()), kind, 1, {})

    async def renew(self, job):
        return True

    async def finish(self, job, succeeded, error=None):
        return True

    async def reconcile(self):
        return 0
