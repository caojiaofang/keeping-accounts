<script setup lang="ts">
import { onLaunch } from '@dcloudio/uni-app'
import { useLedgerStore } from '@/store/ledger'

onLaunch(() => useLedgerStore().hydrate())
</script>

<style lang="scss">
@import './common/styles.scss';
</style>
