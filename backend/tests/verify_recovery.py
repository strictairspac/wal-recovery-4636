"""教学恢复扩展的场景验证：跨检查点提交、同键多次写入、撤销后再断电、完成前中断。

运行：cd backend && python3 tests/verify_recovery.py
不依赖PostgreSQL/Redis，用内存假对象模拟worker的逐步持久化。
"""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from src.services.wal_engine import validate, build_plan, recover, MODEL_VERSION
from src.services.recovery_runner import execute

INITIAL = {'sample_A': 100, 'sample_B': 50}
# T1跨检查点两次写A后提交；T2两次写B未提交；T3写A已提交。检查点LSN3带在途事务T1。
RECORDS = [
    {'op': 'begin', 'tx': 'T1'},
    {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 75},
    {'op': 'checkpoint'},
    {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 80},
    {'op': 'begin', 'tx': 'T2'},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 40},
    {'op': 'commit', 'tx': 'T1'},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 35},
    {'op': 'begin', 'tx': 'T3'},
    {'op': 'write', 'tx': 'T3', 'key': 'sample_A', 'value': 90},
    {'op': 'commit', 'tx': 'T3'},
]

class FakeJob:
    def __init__(self, crash_after, interrupt_after=None):
        self.snapshot = {'log_set': {'initial': copy.deepcopy(INITIAL), 'records': copy.deepcopy(RECORDS)},
                         'case': {'crash_after': crash_after}}
        self.interrupt_after = interrupt_after
        self.progress = None
        self.result = None
        self.status = 'running'

class FakeDB:
    """每次commit抓取持久化深拷贝，并断言完成标志与最终账本同时生效。"""
    def __init__(self, job):
        self.job = job
        self.persisted = []
    def commit(self):
        if self.job.status == 'completed':
            assert self.job.result is not None, '完成标志生效时最终账本必须同事务写入'
        else:
            assert self.job.result is None, '途中状态不得冒充完成（result必须为空）'
        self.persisted.append({'status': self.job.status, 'progress': copy.deepcopy(self.job.progress),
                               'result': copy.deepcopy(self.job.result)})

def run_job(crash_after, interrupt_after=None, crash_at_step=None):
    """模拟worker执行；crash_at_step模拟第N步提交后worker真实断电，随后从持久化进度接回。"""
    job = FakeJob(crash_after, interrupt_after)
    db = FakeDB(job)
    rounds = 0
    while job.status != 'completed':
        rounds += 1
        assert rounds <= 5, '接回次数异常'
        try:
            execute(job, db)
        except RuntimeError:
            pass  # 模拟断电：从最后一次持久化提交恢复
        if crash_at_step is not None and job.progress and job.progress['done_steps'] >= crash_at_step and job.status == 'running':
            # worker在第crash_at_step步提交后被杀：丢弃内存态，回滚到持久化进度
            persisted = db.persisted[-1]
            job.progress = persisted['progress']
            job.result = persisted['result']
            job.status = 'queued'
            crash_at_step = None
        elif job.status == 'interrupted':
            # 用户接回：中断点已消费
            job.interrupt_after = None
            job.status = 'queued'
        if job.status == 'queued':
            job.status = 'running'
    return job, db

def check(name, cond):
    assert cond, f'失败: {name}'
    print(f'  通过: {name}')

print('1. 跨检查点提交与同键多次写入（crash_after=8）')
one_shot = recover(INITIAL, RECORDS, 8)
check('检查点LSN为3且记录在途事务T1', one_shot['checkpoint_lsn'] == 3 and one_shot['checkpoint_active'] == ['T1'])
check('检查点落盘含T1未提交脏值A=75', one_shot['durable_checkpoint'] == {'sample_A': 75, 'sample_B': 50})
check('跨检查点提交的T1两次写A保留为80', one_shot['recovered']['sample_A'] == 80)
check('T2同键两次写B最终撤净回50', one_shot['recovered']['sample_B'] == 50)
check('未提交事务仅T2', one_shot['losers'] == ['T2'] and one_shot['committed'] == ['T1'])
check('撤销按LSN倒序（先LSN8后LSN6）', [t['lsn'] for t in one_shot['trace'] if t['phase'] == 'undo_loser'] == [8, 6])
check('独立oracle一致', one_shot['invariant_passed'] and one_shot['oracle'] == one_shot['recovered'])
check('模型版本', one_shot['model_version'] == MODEL_VERSION)

print('2. 撤销途中再次断电（interrupt_after=5，第5步为首个undo）')
job, db = run_job(8, interrupt_after=5)
mid = [p for p in db.persisted if p['status'] == 'interrupted']
check('第5步后中断且进度已持久化', len(mid) == 1 and mid[0]['progress']['done_steps'] == 5)
check('中断时处于撤销途中（B仅回到40，未撤净）', mid[0]['progress']['state'] == {'sample_A': 80, 'sample_B': 40})
check('中断时不含结果（不冒充完成）', mid[0]['result'] is None)
check('接回后完成且结果与一次性恢复一致', job.result['recovered'] == one_shot['recovered'] and job.result['result_sha256'] == one_shot['result_sha256'])
check('重试不重复撤销：undo_count仍为2', job.result['undo_count'] == 2)
check('重试不累计轨迹：总步数仍为6', len(job.result['trace']) == 6 and job.result['redo_count'] == 4)
check('接回轮次记录为2', job.result['attempts'] == 2)

print('3. 完成前中断（interrupt_after=0，一步未执行即断电）')
job0, db0 = run_job(8, interrupt_after=0)
mid0 = [p for p in db0.persisted if p['status'] == 'interrupted']
check('第0步中断且无任何轨迹', mid0[0]['progress']['done_steps'] == 0 and mid0[0]['progress']['trace'] == [])
check('接回后完整恢复', job0.result['recovered'] == one_shot['recovered'])

print('4. worker在撤销途中真实断电（第5步提交后进程被杀，从持久化进度接回）')
job2, db2 = run_job(8, crash_at_step=5)
check('断电后接回完成，结果一致', job2.result['recovered'] == one_shot['recovered'])
check('断电重试不重复撤销', job2.result['undo_count'] == 2 and len(job2.result['trace']) == 6)

print('5. 完整提交点与部分前缀')
full = recover(INITIAL, RECORDS, 11)
check('全部日志后A=90/B=50（T2始终未提交被撤净）', full['recovered'] == {'sample_A': 90, 'sample_B': 50} and full['losers'] == ['T2'])
ten = recover(INITIAL, RECORDS, 10)
check('T3未提交时A撤到80（T1跨检查点值仍保留）', ten['recovered'] == {'sample_A': 80, 'sample_B': 50} and ten['losers'] == ['T2', 'T3'])
check('无中断时一次性完成', run_job(8)[0].result['attempts'] == 1)

print('6. 严格写锁与校验仍生效')
check('活动中检查点被允许', validate(INITIAL, RECORDS) is True)
def expect_error(name, records, crash_after=None):
    try:
        if crash_after is None:
            validate(INITIAL, records)
        else:
            recover(INITIAL, records, crash_after)
    except ValueError:
        print(f'  通过: {name}')
        return
    raise AssertionError(f'失败: {name}（未拒绝）')
expect_error('同键重叠活动事务违反严格写锁', [{'op': 'begin', 'tx': 'A'}, {'op': 'write', 'tx': 'A', 'key': 'sample_A', 'value': 1}, {'op': 'begin', 'tx': 'B'}, {'op': 'write', 'tx': 'B', 'key': 'sample_A', 'value': 2}])
expect_error('事务标识复用', [{'op': 'begin', 'tx': 'A'}, {'op': 'commit', 'tx': 'A'}, {'op': 'begin', 'tx': 'A'}])
expect_error('写入未知键', [{'op': 'begin', 'tx': 'A'}, {'op': 'write', 'tx': 'A', 'key': 'nope', 'value': 1}])
expect_error('崩溃点超限', RECORDS, crash_after=12)
expect_error('未begin先写', [{'op': 'write', 'tx': 'A', 'key': 'sample_A', 'value': 1}])

print('7. 中断位置越界等同于不中断')
job3, _ = run_job(8, interrupt_after=6)
check('interrupt_after>=总步数直接完成', job3.status == 'completed' and job3.result['recovered'] == one_shot['recovered'])

print('\n全部场景验证通过。')
