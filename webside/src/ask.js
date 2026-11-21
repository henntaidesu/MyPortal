import { shallowRef } from 'vue'

/**
 * 页面内的确认框 / 输入框，替掉浏览器自带的 confirm() / prompt()。
 *
 * 自带那两个长什么样由浏览器说了算：顶上压着一行「xxx.com 显示」，按钮和字体
 * 跟门户其余的弹窗对不上，暗色下更扎眼；输入框还没法设成 password，
 * 重置口令时明文摊在屏幕上。
 *
 * 用法和原来差不多，只是变成了 await：
 *
 *   if (!(await askConfirm({ title: '删除', message: '删掉？', danger: true }))) return
 *   const pw = await askText({ title: '新密码', type: 'password' })   // 取消 = null
 *
 * 状态单独放一个模块、由 App.vue 挂一个 AskDialog 画出来，和 drag.js 一个路数：
 * 哪个组件都能调，不用一层层往下传 props / emit。
 */
export const current = shallowRef(null)

function open(req, cancelValue) {
  // 同时只开一个。前一个还没答就当它取消了，别让它的 await 永远挂着
  current.value?.answer(current.value.cancelValue)
  return new Promise((resolve) => {
    const me = {
      ...req,
      cancelValue,
      answer(v) {
        if (current.value === me) current.value = null
        resolve(v)
      }
    }
    current.value = me
  })
}

/**
 * 确认框。答「确定」是 true，取消 / Esc / 点遮罩是 false。
 * opts：title、message、ok（按钮上的字）、danger（按钮画成红的）
 */
export function askConfirm(opts) {
  return open({ kind: 'confirm', ok: '确定', ...opts }, false)
}

/**
 * 输入框。答「确定」是输进去的字符串（可能是空串），取消是 null。
 * opts：title、message、label、value（初始值）、type（text / password）、ok
 */
export function askText(opts) {
  return open({ kind: 'text', ok: '确定', value: '', type: 'text', ...opts }, null)
}
