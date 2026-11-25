# 存钱助手滚动与底部裁切修复

2026-11-16，修复「聊聊花费 → 家庭存钱助手 → 消费体检」无法上下滑动、长内容底部被裁切的问题。属于小程序界面修复，需要重新编译上传客户端，后端无需更新。

## 改动

`components/assistant/SavingsAssistant.vue` 原先没有声明 `virtualHost`，微信编译后保留自定义组件宿主，工作区不能直接作为父级 flex 的剩余高度项；H5 的组件根节点行为不同，因此原先的 H5 点击验收没有发现这一差异。

- 增加 `virtualHost: true`，与外层 `ExpenseChat`、`QuickRecord` 一致。
- 使用独立普通 `view.workspace-viewport` 承接剩余高度，原生 `scroll-view` 使用明确的 `height: 100%`，不再直接依靠滚动组件自身的零高度 flex 布局。
- 工作区限制溢出，页签栏不收缩；滚动内容保留原有底部留白和安全区。

三个页签共用该滚动区，消费体检、展开目标设置、下月安排及跟进复盘都采用相同的高度约束。

## 验证

使用十二项虚构消费，拦截全部业务请求，不访问正式账单或模型。Playwright 通过真实滚轮事件滑到底部、再回到顶部，核对文档本身不滚动、视口高度不随滚动变化、底部说明完整可见：

| H5 视窗 | 滚动视口高度 | 到底后的 scrollTop | 底部说明留白 |
| --- | ---: | ---: | ---: |
| 390 × 844 | 586.875 px | 3191 px | 约 21 px |
| 320 × 568 | 351.734 px | 2750 px | 约 17 px |
| 390 × 420 | 162.875 px | 3615 px | 约 21 px |

展开目标设置后仍可滑到底部；切至下月安排后可在 587 px 视口中滚动完整的 963 px 内容，返回顶部正常。短屏验证不等同于真实手机键盘验收。

微信产物已核对包含 `options.virtualHost` 及明确高度的滚动容器，并在独立游客工程观察到完整底部说明。原生手势自动化未完成，微信真机上下滑动及键盘场景仍需复测。

检查：前端 `npm run check`、微信小程序和 H5 构建；H5 测量日志为 `output/playwright/savings-scroll-results.log`，截图为 `output/playwright/savings-scroll-*-bottom.png`。独立微信预览截图为 `output/wechat-savings-scroll/state.png`。本次未上传小程序。
