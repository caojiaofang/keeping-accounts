// 账号维护功能依赖新的管理接口与数据库迁移，正式构建需显式开启。
// 当前生产服务仍使用原管理员实现，默认仅发布已支持的查询页面。
export const accountFeaturesEnabled = import.meta.env.DEV || import.meta.env.VITE_ADMIN_ACCOUNT_FEATURES === 'true'
