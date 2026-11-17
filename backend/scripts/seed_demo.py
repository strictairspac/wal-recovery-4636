import os
from sqlalchemy import select, text
from src.config.database import Base,engine,Session
from src.models.entities import User,LogSet,CrashCase
from src.utils.security import hash_password
RECORDS=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'sample_A','value':75},{'op':'commit','tx':'T1'},{'op':'checkpoint'},{'op':'begin','tx':'T2'},{'op':'write','tx':'T2','key':'sample_B','value':40},{'op':'begin','tx':'T3'},{'op':'write','tx':'T3','key':'sample_A','value':60},{'op':'commit','tx':'T3'},{'op':'write','tx':'T2','key':'sample_B','value':35},{'op':'commit','tx':'T2'}]
# 活动中检查点：T1跨检查点提交并两次写同键，T2未提交，演示撤销途中再次断电。
RECORDS_OPEN=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'sample_A','value':75},{'op':'checkpoint'},{'op':'write','tx':'T1','key':'sample_A','value':80},{'op':'begin','tx':'T2'},{'op':'write','tx':'T2','key':'sample_B','value':40},{'op':'commit','tx':'T1'},{'op':'write','tx':'T2','key':'sample_B','value':35},{'op':'begin','tx':'T3'},{'op':'write','tx':'T3','key':'sample_A','value':90},{'op':'commit','tx':'T3'}]
def migrate():
    # 幂等补列：已有数据卷保留旧演练记录，无需down -v。
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE recoveries ADD COLUMN IF NOT EXISTS interrupt_after INTEGER'))
        conn.execute(text('ALTER TABLE recoveries ADD COLUMN IF NOT EXISTS progress JSON'))
def seed():
    Base.metadata.create_all(engine)
    migrate()
    if os.getenv('SEED_DEMO','true').lower()!='true':return
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@haili.local'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,role=role,password_hash=hash_password('Recovery2026!')))
        logs=db.scalar(select(LogSet).where(LogSet.name=='海砾采样账本-晚班'))
        if logs is None:
            logs=LogSet(name='海砾采样账本-晚班',description='A初始100份、B初始50份，T1提交后检查点，T2未提交，T3已提交',initial={'sample_A':100,'sample_B':50},records=RECORDS);db.add(logs);db.flush()
        for name,point in [('断电点10-撤销T2',10),('完整提交点11',11),('初始点0',0)]:
            if not db.scalar(select(CrashCase).where(CrashCase.name==name)):db.add(CrashCase(name=name,log_set_id=logs.id,crash_after=point))
        open_logs=db.scalar(select(LogSet).where(LogSet.name=='海砾采样账本-跨检查点'))
        if open_logs is None:
            open_logs=LogSet(name='海砾采样账本-跨检查点',description='T1跨检查点两次写A后提交，T2两次写B未提交，T3写A已提交；检查点LSN3带在途事务',initial={'sample_A':100,'sample_B':50},records=RECORDS_OPEN);db.add(open_logs);db.flush()
        for name,point in [('跨检查点断电点8-撤销T2',8),('跨检查点完整点11',11)]:
            if not db.scalar(select(CrashCase).where(CrashCase.name==name)):db.add(CrashCase(name=name,log_set_id=open_logs.id,crash_after=point))
        db.commit()
if __name__=='__main__':seed()
