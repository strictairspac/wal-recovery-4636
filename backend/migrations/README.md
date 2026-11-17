# 初始化结构
SQLAlchemy metadata.create_all首次建users、log_sets、crash_cases、recoveries、audits。log_sets.name、crash_cases.name唯一，关联使用外键，入队冻结日志和崩溃点快照。recoveries含interrupt_after（恢复第N步后再断电）与progress（按步持久化的恢复进度）两列，由seed_demo以ALTER TABLE IF NOT EXISTS幂等补列，旧演练记录保留。暂无在线schema升级器。
