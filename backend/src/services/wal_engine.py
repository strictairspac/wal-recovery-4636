"""Resumable WAL teaching model: active-transaction checkpoints, strict write locks, step-persisted recovery."""
import copy,hashlib,json,re

MODEL_VERSION='strict-wal-resumable-v2'

def validate(initial,records):
    if not initial or len(initial)>30 or not records or len(records)>200: raise ValueError('需1—30个键及1—200条日志')
    for key,value in initial.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}',key) or isinstance(value,bool) or not isinstance(value,int) or abs(value)>1000000000:
            raise ValueError('初始键为字母数字下划线短横线，值为绝对值不超过10亿的整数')
    state=copy.deepcopy(initial); active={};used=set();locks={}
    for lsn,r in enumerate(records,1):
        op=r.get('op');tx=r.get('tx')
        if op=='checkpoint':
            # 活动中检查点：允许未结束事务，落盘快照含在途写入，在途事务由恢复阶段判定去留。
            continue
        if not tx:raise ValueError(f'LSN {lsn}: 需要事务标识')
        if op=='begin':
            if tx in used:raise ValueError(f'LSN {lsn}: 事务标识不得重复')
            active[tx]=[];used.add(tx)
        elif op=='write':
            if tx not in active:raise ValueError(f'LSN {lsn}: 写入事务尚未begin')
            key=r.get('key');value=r.get('value')
            if key not in initial:raise ValueError(f'LSN {lsn}: 写入键必须存在于初始账本')
            if isinstance(value,bool) or not isinstance(value,int) or abs(value)>1000000000:raise ValueError(f'LSN {lsn}: 数值超限')
            if key in locks and locks[key]!=tx:raise ValueError(f'LSN {lsn}: 违反严格写锁，活动事务持有同键')
            locks[key]=tx;active[tx].append((key,state[key]));state[key]=value
        elif op in ('commit','abort'):
            if tx not in active:raise ValueError(f'LSN {lsn}: 事务未开启或已结束')
            if op=='abort':
                for key,before in reversed(active[tx]):state[key]=before
            del active[tx]
            locks={k:v for k,v in locks.items() if v!=tx}
        else:raise ValueError(f'LSN {lsn}: 未知操作')
    return True

def build_plan(initial,records,crash_after):
    """Validate and expand recovery into a deterministic step list for resumable execution."""
    validate(initial,records)
    if not isinstance(crash_after,int) or not 0<=crash_after<=len(records):raise ValueError('崩溃点超出日志范围')
    prefix=records[:crash_after]; state=copy.deepcopy(initial); active={}; winners=[];aborted=[];checkpoint=0;checkpoint_active=[];durable=copy.deepcopy(initial);steps=[]
    for lsn,r in enumerate(prefix,1):
        op=r['op'];tx=r.get('tx')
        if op=='begin':active[tx]=[]
        elif op=='write':
            row={'lsn':lsn,'tx':tx,'key':r['key'],'before':state[r['key']],'after':r['value']}
            active[tx].append(row);steps.append(dict(row,phase='redo'));state[r['key']]=r['value']
        elif op=='abort':
            for row in reversed(active[tx]):
                steps.append(dict(row,phase='redo_abort_undo'));state[row['key']]=row['before']
            del active[tx];aborted.append(tx)
        elif op=='commit':winners.append(tx);del active[tx]
        elif op=='checkpoint':
            # Quiescent no longer required: snapshot pages as-is (steal) and remember in-flight transactions.
            checkpoint=lsn;checkpoint_active=sorted(active);durable=copy.deepcopy(state)
    # Recovery repeats history from the initial pages, then undoes every unfinished write in reverse LSN order.
    after_redo=copy.deepcopy(state);losers=sorted(active)
    undo=sorted([w for tx in active for w in active[tx]],key=lambda w:w['lsn'],reverse=True)
    for row in undo:
        steps.append(dict(row,phase='undo_loser'));state[row['key']]=row['before']
    # Independent oracle from the initial ledger applies only writes of transactions committed inside the prefix.
    oracle=copy.deepcopy(initial);committed=set(winners)
    for r in prefix:
        if r['op']=='write' and r['tx'] in committed:oracle[r['key']]=r['value']
    return {'initial':copy.deepcopy(initial),'steps':steps,'final':state,'after_redo':after_redo,
            'checkpoint_lsn':checkpoint,'checkpoint_active':checkpoint_active,'durable_checkpoint':durable,
            'committed':winners,'aborted':aborted,'losers':losers,'oracle':oracle}

def apply_step(state,step):
    if step['phase']=='redo':state[step['key']]=step['after']
    else:state[step['key']]=step['before']

def result_payload(initial,records,crash_after,plan,trace,attempts=1):
    final=plan['final'];checksum=hashlib.sha256(json.dumps(final,sort_keys=True).encode()).hexdigest()
    source=json.dumps({'initial':initial,'records':records,'crash_after':crash_after},sort_keys=True)
    return {'simulation':True,'model_version':MODEL_VERSION,'crash_after':crash_after,'checkpoint_lsn':plan['checkpoint_lsn'],
            'checkpoint_active':plan['checkpoint_active'],'durable_checkpoint':plan['durable_checkpoint'],
            'after_redo':plan['after_redo'],'recovered':final,'oracle':plan['oracle'],'invariant_passed':final==plan['oracle'],
            'committed':plan['committed'],'aborted':plan['aborted'],'losers':plan['losers'],
            'total_steps':len(plan['steps']),'attempts':attempts,
            'redo_count':len([t for t in trace if t['phase']=='redo']),
            'undo_count':len([t for t in trace if t['phase']=='undo_loser']),
            'trace':trace,'result_sha256':checksum,'input_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'boundary':'Python WAL教学模型；严格写锁、活动中检查点、按步持久化恢复、整数键值、日志前缀持久。没有注入真实PostgreSQL崩溃，未执行数据库物理恢复。'}

def recover(initial,records,crash_after):
    """One-shot recovery: run every planned step without interruption."""
    plan=build_plan(initial,records,crash_after)
    state=copy.deepcopy(plan['initial']);trace=[]
    for step in plan['steps']:
        apply_step(state,step);trace.append(step)
    return result_payload(initial,records,crash_after,plan,trace)
