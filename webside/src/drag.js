import { reactive } from 'vue'
import { moveItem, moveGroup, groupIndex } from './store'

/**
 * 拖拽状态。单独一个模块，不放 store.js：那边是要存盘的数据，这边纯粹是鼠标此刻的位置。
 * 也不从 App.vue 一层层往下传——分类大卡和卡片都要读它，传 props 得穿两层、六七个。
 *
 * 两种拖法共用这一份状态，靠 `kind` 分开：
 *   card   拖卡片。落在别的卡片上 = 插到它前面；落在分类的空白处 = 放到那个分类末尾。
 *          跨分类拖就是「换个分类」，不用先剪切再粘贴。
 *   group  拖分类。只认分类头上那个 ⠿ 把手——整张头都可拖的话，
 *          里面那个 contenteditable 的分类名就没法用鼠标选字了。
 *
 * 用 HTML5 拖放但**不走 dataTransfer**：它只能传字符串，而且 dragover 时读不出来
 * （规范不让），没法一边拖一边判断该不该高亮。
 */
export const drag = reactive({
  kind: '',          // '' | 'card' | 'group'
  fromGid: '',       // kind=card：卡片原来在哪个分类
  fromIndex: -1,     // kind=card：在原分类里的下标；kind=group：分类自己的下标
  overGid: '',       // 正悬停在哪个分类上（整张大卡高亮）
  overIndex: -1      // 正悬停在哪张卡片上（插入位置），-1 表示只在分类空白处
})

export function startCard(gid, index) {
  drag.kind = 'card'
  drag.fromGid = gid
  drag.fromIndex = index
}

export function startGroup(index) {
  drag.kind = 'group'
  drag.fromGid = ''
  drag.fromIndex = index
}

/** 悬停到某张卡片上。搜索状态下不排序，调用方会先挡掉 */
export function overCard(gid, index) {
  if (drag.kind !== 'card') return
  drag.overGid = gid
  drag.overIndex = index
}

/** 悬停到分类上（拖卡片时是「放到末尾」，拖分类时是「插到这个分类前面」） */
export function overGroup(gid) {
  if (!drag.kind) return
  drag.overGid = gid
  if (drag.kind === 'card') drag.overIndex = -1
}

export function leaveGroup(gid) {
  if (drag.overGid === gid) {
    drag.overGid = ''
    drag.overIndex = -1
  }
}

/** 落在某张卡片上：插到它前面 */
export function dropOnCard(gid, index) {
  if (drag.kind === 'card') moveItem(drag.fromGid, drag.fromIndex, gid, index)
  end()
}

/** 落在分类上：拖卡片就放到末尾，拖分类就和它换位置 */
export function dropOnGroup(gid) {
  if (drag.kind === 'card') {
    moveItem(drag.fromGid, drag.fromIndex, gid, -1)
  } else if (drag.kind === 'group') {
    const to = groupIndex(gid)
    if (to > -1) moveGroup(drag.fromIndex, to)
  }
  end()
}

export function end() {
  drag.kind = ''
  drag.fromGid = ''
  drag.fromIndex = -1
  drag.overGid = ''
  drag.overIndex = -1
}
