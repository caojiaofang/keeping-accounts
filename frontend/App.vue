<script setup lang="ts">
import { onLaunch, onShow } from '@dcloudio/uni-app'
import { useAuthStore } from '@/store/auth'
import { rememberInvitation } from '@/common/services/invitations'

onLaunch(options => {
  if (options?.path === 'pages/book-invite/index') rememberInvitation(options.query?.token)
  void useAuthStore().bootstrap()
})
onShow(() => { void useAuthStore().verifyIfNeeded() })
</script>

<style lang="scss">
@import './common/styles/index.scss';
</style>
