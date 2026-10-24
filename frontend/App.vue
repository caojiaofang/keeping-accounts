<script setup lang="ts">
import { onLaunch, onShow } from '@dcloudio/uni-app'
import { useAuthStore } from '@/store/auth'

onLaunch(() => { void useAuthStore().bootstrap() })
onShow(() => { void useAuthStore().verifyIfNeeded() })
</script>

<style lang="scss">
@import './common/styles/index.scss';
</style>
