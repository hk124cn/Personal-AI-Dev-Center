// 首页同步状态条（renderLastSyncBar）分支自测
// 直接从 index.html 抽取真实函数体，套最小 stub 运行，避免手写副本与源码漂移。
//
// 用法: node tools/test_syncbar.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '..', 'index.html'), 'utf8');

// ---- 1. 抽取源码片段 ------------------------------------------------------ //
const start = html.indexOf('const SYNCBAR_KEY=');
const end = html.indexOf('function _freqText');
if (start < 0 || end < 0 || end <= start) {
  console.error('FAIL: 无法定位 renderLastSyncBar 代码块（SYNCBAR_KEY / _freqText）');
  process.exit(1);
}
const snippet = html.slice(start, end);

// ---- 2. 最小 stub -------------------------------------------------------- //
// t() 用一份小词典代替真实 I18N，让断言能直接写中文文案；未命中的键原样返回。
const ZH = {
  ls_bar_title: '最近同步', ls_success: '成功', ls_failed: '失败',
  ls_analyzed: 'LLM 分析', ls_none: '暂无任何同步记录',
  lsb_show: '显示同步状态', lsb_hide: '隐藏', lsb_expand: '展开详情', lsb_collapse: '收起',
};
let mode = 'collapsed';
const store = {
  getItem: () => mode,
  setItem: (_k, v) => { mode = v; },
};
const sandbox = {
  localStorage: store,
  render: () => {},
  t: (k) => ZH[k] ?? k,
  icon: (n) => `<i data-lucide="${n}"></i>`,
  escapeHtml: (s) => String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
  relativeTime: () => '2 小时前',
  console,
};
vm.createContext(sandbox);
vm.runInContext(snippet + '\nthis.__f = renderLastSyncBar; this.__toggle = toggleSyncBar; this.__hide = hideSyncBar; this.__setMode = setSyncBarMode;', sandbox);
const render = sandbox.__f;

// ---- 3. 断言 ------------------------------------------------------------- //
const OK = { finished_at: '2026-09-17T10:00:00Z', started_at: '2026-09-17T09:58:00Z', source: 'auto', success: 12, failed: 0, total: 12, llm_analyzed: 3, duration_sec: 41 };
const BAD = { ...OK, success: 11, failed: 1, errors: [{ name: 'demo', error: 'timeout' }] };

let pass = 0, fail = 0;
function check(label, cond, extra = '') {
  if (cond) { pass++; console.log(`  PASS  ${label}`); }
  else { fail++; console.log(`  FAIL  ${label}${extra ? '  <- ' + extra : ''}`); }
}
function has(hay, needle) { return hay.includes(needle); }

console.log('\n[1] 首页默认（collapsed）· 全部成功');
mode = 'collapsed';
let out = render(OK, 'compact');
check('使用 lsb-compact', has(out, 'lsb-compact'));
check('不带 lsb-expanded', !has(out, 'lsb-expanded'));
check('单行摘要含标题/时间/成功数', has(out, '最近同步') && has(out, '2 小时前') && has(out, '12'), out.replace(/\s+/g, ' ').slice(0, 220));
check('有展开按钮 chevron-down', has(out, 'chevron-down'));
check('有隐藏按钮 x', has(out, 'data-lucide="x"'));
check('成功态带 lsb-allok', has(out, 'lsb-allok'));
check('折叠态不渲染长提示 hint', !has(out, 'lsb-hint'));
check('无 undefined/NaN', !has(out, 'undefined') && !has(out, 'NaN'), out.slice(0, 200));

console.log('\n[2] 首页 collapsed · 有失败');
mode = 'collapsed';
out = render(BAD, 'compact');
check('带 lsb-haserr', has(out, 'lsb-haserr'));
check('摘要突出失败数（lsb-bad-text）', has(out, 'lsb-bad-text'));
check('摘要含失败文案', has(out, '失败'));

console.log('\n[3] 首页展开（expanded）');
mode = 'expanded';
out = render(BAD, 'compact');
check('带 lsb-expanded', has(out, 'lsb-expanded'));
check('渲染完整 body（lsb-title/lsb-meta）', has(out, 'lsb-title') && has(out, 'lsb-meta'));
check('渲染 hint', has(out, 'lsb-hint'));
check('渲染错误列表', has(out, 'lsb-errors') && has(out, 'timeout'));
check('有收起按钮 chevron-up', has(out, 'chevron-up'));

console.log('\n[4] 首页隐藏（hidden）');
mode = 'hidden';
out = render(OK, 'compact');
check('只渲染 mini 入口', has(out, 'lsb-mini'));
check('不再渲染状态条主体', !has(out, 'last-sync-bar'));
check('点击可恢复（setSyncBarMode collapsed）', has(out, "setSyncBarMode('collapsed')"));

console.log('\n[5] 定时同步页（完整形态，不受模式影响）');
mode = 'hidden';   // 即使在首页隐藏状态下，管理页也要照常显示
out = render(OK);
check('不带 lsb-compact', !has(out, 'lsb-compact'));
check('不出 mini', !has(out, 'lsb-mini'));
check('渲染完整 body', has(out, 'lsb-title') && has(out, 'lsb-hint'));
check('无收起按钮', !has(out, 'chevron-up'));

console.log('\n[6] 从未同步过');
mode = 'collapsed';
out = render(null, 'compact');
check('compact 未同步也是一行', has(out, 'lsb-compact') && has(out, '暂无任何同步记录'));
check('未同步可折叠/隐藏', has(out, 'lsb-cbtn') && has(out, 'data-lucide="x"'));
mode = 'collapsed';
out = render(null);
check('full 未同步保持原样式', has(out, 'last-sync-bar') && has(out, '暂无任何同步记录') && !has(out, 'lsb-compact'));

console.log('\n[7] 交互函数写回 localStorage');
mode = 'collapsed';
sandbox.__toggle();
check('toggle: collapsed -> expanded', mode === 'expanded', `mode=${mode}`);
sandbox.__toggle();
check('toggle: expanded -> collapsed', mode === 'collapsed', `mode=${mode}`);
sandbox.__hide();
check('hide -> hidden', mode === 'hidden', `mode=${mode}`);
sandbox.__setMode('collapsed');
check('恢复 -> collapsed', mode === 'collapsed', `mode=${mode}`);

console.log(`\n结果: ${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);
