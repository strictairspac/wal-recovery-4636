import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {recoveriesAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {esc,formObject} from '../utils/html.js';
import {action} from '../hooks/action.js';
export function runsPage(){return `<section class="split compact"><div><h2>发起一次恢复演练</h2><p>提交时冻结日志与崩溃点，后台按步重做、撤销并持久保存进度；可设中断位置，演练撤销途中再次断电后接回。</p></div>${session.role!=='viewer'?`<form id="run-form"><label>故障场景<select name="crash_case_id">${store.cases.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></label><label>记录标签<input name="label" value="晚班恢复核对" required></label><label>中断位置（恢复第N步后再断电，留空不中断）<input name="interrupt_after" type="number" min="0" max="1000" placeholder="不中断"></label><button type="submit">冻结日志并执行 →</button></form>`:'<p class="readonly">观察员可查看下方恢复结果。</p>'}</section><div class="section-title"><h2>恢复队列</h2><button class="secondary" id="refresh">刷新状态</button></div>${table(['编号','恢复记录','状态','中断点','恢复核对','证据'],store.runs.map(r=>[r.id,esc(r.label),badge(r.status),r.interrupt_after??'—',r.status==='completed'?(r.result?.invariant_passed?'与提交事务一致':'需检查'):r.status==='interrupted'?`已执行${r.progress?.done_steps??0}/${r.progress?.total_steps??'?'}步，可接回`:r.result?.error?'校验失败':'待计算',`<a href="#detail/${r.id}">查看恢复轨迹 →</a>`]))}`;}
export function bindRuns(render){document.querySelector('#refresh').onclick=render;action(document.querySelector('#run-form'),async f=>{const d=formObject(f);d.crash_case_id=Number(d.crash_case_id);if(d.interrupt_after==='')delete d.interrupt_after;else d.interrupt_after=Number(d.interrupt_after);const job=await recoveriesAPI.create(d);location.hash='#detail/'+job.id;});}
