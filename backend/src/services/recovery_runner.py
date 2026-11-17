"""Step-wise resumable recovery execution shared by the worker and verification."""
from src.services.wal_engine import build_plan, apply_step, result_payload

def execute(job, db):
    """Run the recovery step by step, persisting progress after every step.

    The plan is deterministic, so a resumed job continues from the persisted
    step counter instead of replaying finished work: retries never undo twice
    or inflate redo/undo counts. The completion flag and the final ledger are
    written in the same commit; interrupted jobs keep a queryable progress
    state without a result payload.
    """
    snap = job.snapshot
    initial = snap['log_set']['initial']
    records = snap['log_set']['records']
    crash_after = snap['case']['crash_after']
    plan = build_plan(initial, records, crash_after)
    steps = plan['steps']
    progress = job.progress or {'done_steps': 0, 'state': plan['initial'], 'trace': [], 'attempts': 0}
    done = progress['done_steps']
    state = progress['state']
    trace = progress['trace']
    attempts = progress['attempts'] + 1
    limit = job.interrupt_after
    while True:
        if done >= len(steps):
            job.result = result_payload(initial, records, crash_after, plan, trace, attempts)
            job.progress = {'done_steps': done, 'state': state, 'trace': trace, 'attempts': attempts, 'total_steps': len(steps)}
            job.status = 'completed'
            db.commit()
            return
        if limit is not None and done >= limit:
            job.progress = {'done_steps': done, 'state': state, 'trace': trace, 'attempts': attempts, 'total_steps': len(steps)}
            job.status = 'interrupted'
            db.commit()
            return
        step = steps[done]
        apply_step(state, step)
        trace.append(step)
        done += 1
        job.progress = {'done_steps': done, 'state': state, 'trace': trace, 'attempts': attempts, 'total_steps': len(steps)}
        db.commit()
