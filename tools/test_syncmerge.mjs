// 同步结果合并（mergeAPIData / sanitizeFeatures / progressBadgesHTML）回归自测
//
// 覆盖 2026-09-22 修掉的两个问题：
//   ① 远端「问题」曾被塞进 features，并经「编辑项目」持久化到 config.json，
//      把真实功能清单整份覆盖掉（曾污染 9 个项目共 292 条）；
//   ② 「内容更新时间」此前只有远端 md 里手写的「最后更新」一个来源，
//      而没人写那行 → 该字段永远是死的。现在改用同步探测的 content_updated_at。
//
// 直接从 index.html 抽真实函数体，避免手写副本与源码漂移。
// 用法: node tools/test_syncmerge.mjs [--source <html>]
//   --source 可传一份旧稿（如 git show HEAD:index.html > /tmp/old.html），
//   用来验证本测试确实能测出旧行为（应当 FAIL）。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const srcIdx = argv.indexOf('--source');
const target = srcIdx >= 0 && argv[srcIdx + 1]
  ? argv[srcIdx + 1]
  : join(here, '..', 'index.html');
const html = readFileSync(target, 'utf8');
console.log('source:', target);

// ---- 1. 抽取源码片段 ------------------------------------------------------ //
function cut(from, to, label) {
  const a = html.indexOf(from);
  const b = html.indexOf(to);
  if (a < 0 || b < 0 || b <= a) {
    console.error(`FAIL: 无法定位代码块 ${label}（找不到 "${from}" 或 "${to}"）`);
    process.exit(1);
  }
  return html.slice(a, b);
}
const snippetMerge = cut('function isIssueEntry(f){', 'async function triggerSync()', 'mergeAPIData');
const snippetBadge = cut('function progressBadgesHTML(p){', '// ==================== STATS HISTORY', 'progressBadgesHTML');

// ---- 2. 最小 stub -------------------------------------------------------- //
const ZH = { pd_todos: '待办事项', pd_issues: '当前问题' };
const sandbox = {
  t: (k) => ZH[k] ?? k,
  icon: (n) => `<i data-lucide="${n}"></i>`,
  console,
};
vm.createContext(sandbox);
vm.runInContext(
  snippetMerge + '\nthis.__merge = mergeAPIData; this.__san = sanitizeFeatures; this.__isIssue = isIssueEntry;',
  sandbox,
);
vm.runInContext(snippetBadge + '\nthis.__badge = progressBadgesHTML;', sandbox);

const merge = sandbox.__merge;
const san = sandbox.__san;
const isIssue = sandbox.__isIssue;
const badge = sandbox.__badge;

// ---- 3. 断言 ------------------------------------------------------------- //
let pass = 0, fail = 0;
function check(label, cond, extra = '') {
  if (cond) { pass++; console.log(`  PASS  ${label}`); }
  else { fail++; console.log(`  FAIL  ${label}${extra ? '  <- ' + extra : ''}`); }
}
const has = (hay, needle) => String(hay).includes(needle);

console.log('\n[1] isIssueEntry 前缀识别');
check('[问题] xxx 命中', isIssue({ name: '[问题] xxx' }));
check('[Issue] xxx 命中', isIssue({ name: '[Issue] xxx' }));
check('[问题]无空格也命中', isIssue({ name: '[问题]无空格' }));
check('普通功能不命中', !isIssue({ name: '用户注册/登录' }));
check('null / 字符串不崩', !isIssue(null) && !isIssue('x'));

console.log('\n[2] sanitizeFeatures 清洗');
const mixed = [
  { name: '真实功能A', done: true },
  { name: '[问题] 架构说明被误当问题' },
  { name: '[Issue] legacy' },
  { name: '真实功能B' },
  null, 'junk', { done: false },
];
const clean = san(mixed);
check('只留 2 条真实功能', clean.length === 2, JSON.stringify(clean));
check('保留 done 标记', clean[0].done === true);
check('非数组入参返回空数组', san(undefined).length === 0 && san(null).length === 0);

console.log('\n[3] mergeAPIData：问题不再污染功能清单');
const local = {
  id: 'p1',
  features: [{ name: '真实功能', done: false }, { name: '[问题] 历史脏数据', done: false }],
  progress: 70,
  todos: [],
};
const apiP = {
  id: 'p1',
  progress: { progress: null, last_update: '' },
  issues: { current: ['磁盘快满', '证书将过期'] },
  content_updated_at: '2026-09-22T17:00:00+09:00',
  synced_at: '2026-09-22T17:01:00',
  todos: { pending: [{ name: 'a' }], completed: [{ name: 'b' }, { name: 'c' }] },
};
const state = { projects: [local] };
vm.runInContext('', sandbox);
const sandbox2 = {
  t: (k) => ZH[k] ?? k,
  icon: (n) => `<i data-lucide="${n}"></i>`,
  state,
  console,
};
vm.createContext(sandbox2);
vm.runInContext(snippetMerge + '\nthis.__merge = mergeAPIData;', sandbox2);
sandbox2.__merge([apiP]);

check('问题存进 _issues', Array.isArray(local._issues) && local._issues.length === 2, JSON.stringify(local._issues));
check('features 不含 [问题] 条目', local.features.every(f => !has(f.name, '[问题]')), JSON.stringify(local.features));
check('features 只剩 1 条真实功能', local.features.length === 1);
check('features 长度不被问题数抬高', local.features.length !== 1 + 2);
check('todos 已合并（1 待办 / 2 已完成）', local.todos.length === 3, JSON.stringify(local.todos));

console.log('\n[4] mergeAPIData：内容更新时间换源');
check('优先用 content_updated_at', local.lastUpdate === '2026-09-22T17:00:00+09:00', String(local.lastUpdate));
check('progress 为 null 时不冲掉手填进度', local.progress === 70, String(local.progress));

const local2 = { id: 'p2', features: [], progress: 30, todos: [], lastUpdate: '2026-01-01T00:00:00Z' };
const sb3 = { t: (k) => ZH[k] ?? k, icon: (n) => '', state: { projects: [local2] }, console };
vm.createContext(sb3);
vm.runInContext(snippetMerge + '\nthis.__merge = mergeAPIData;', sb3);
sb3.__merge([{
  id: 'p2',
  progress: { progress: 55, last_update: '2026-08-01T10:00:00' },
  issues: { current: [] },
  content_updated_at: null,
}]);
check('无 content_updated_at 时回退手写最后更新', local2.lastUpdate === '2026-08-01T10:00:00', String(local2.lastUpdate));
check('progress=55 正常覆盖', local2.progress === 55, String(local2.progress));
check('issues 为空时 _issues 清空', Array.isArray(local2._issues) && local2._issues.length === 0);

const local3 = { id: 'p3', features: [], progress: 40, todos: [], lastUpdate: '2026-01-01T00:00:00Z' };
const sb4 = { t: (k) => ZH[k] ?? k, icon: (n) => '', state: { projects: [local3] }, console };
vm.createContext(sb4);
vm.runInContext(snippetMerge + '\nthis.__merge = mergeAPIData;', sb4);
sb4.__merge([{ id: 'p3', progress: { progress: 0, last_update: '' }, issues: null, content_updated_at: null }]);
check('两个时间源都缺时保留原值', local3.lastUpdate === '2026-01-01T00:00:00Z', String(local3.lastUpdate));
check('progress=0 不冲掉手填进度（历史行为）', local3.progress === 40, String(local3.progress));

console.log('\n[5] progressBadgesHTML 首页徽标');
const b1 = badge({ todos: [{ done: true }, { done: false }, { done: false }, { done: false }], _issues: ['a', 'b', 'c', 'd', 'e'] });
check('待办徽标显示 1/4', has(b1, '1/4'), b1);
check('待办徽标用 list-checks 图标', has(b1, 'list-checks'));
check('问题徽标显示 5', has(b1, 'alert-triangle') && has(b1, '>5<'), b1);
check('问题徽标带 warn 高亮', has(b1, 'li-badge warn'));
const b2 = badge({ todos: [], _issues: [] });
check('两者皆空时不渲染任何徽标', b2 === '', JSON.stringify(b2));
const b3 = badge({});
check('字段缺失时不崩', b3 === '');

console.log(`\n结果: ${pass} 通过 / ${fail} 失败\n`);
process.exit(fail === 0 ? 0 : 1);
