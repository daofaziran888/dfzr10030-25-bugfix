<template>
  <section class="page" data-module="monitor">
    <header class="page-head">
      <div>
        <h2>采集监控</h2>
        <p class="page-desc">
          每个面板绑定一个采集批次，成功率与平均延迟按批次汇总成一份快照；同一批次重复刷新读到同一份指标。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="refresh-snapshot" @click="refreshAll">刷新面板</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出批次快照</button>
      </div>
    </header>

    <div class="analysis-summary">
      <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
        <strong style="font-size:22px;display:block">{{ formatMetric(m) }}</strong>
        <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">
          {{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%
        </span>
      </article>
    </div>

    <form class="analysis-form" @submit.prevent="runAnalysis">
      <label>
        <span>批次号</span>
        <select v-model="params['批次号']" data-testid="batch-select">
          <option value="">全部批次</option>
          <option v-for="batch in batches" :key="batch.批次号" :value="batch.批次号">
            {{ batch.批次号 }} · {{ batch.采集源 }}
          </option>
        </select>
      </label>
      <label>
        <span>指标口径</span>
        <input value="按采集批次汇总（事件时间归口）" readonly />
      </label>
      <label>
        <span>快照指纹</span>
        <input :value="currentFingerprint" readonly data-testid="snapshot-fingerprint" />
      </label>
      <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
    </form>

    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }">
        <span>{{ bar.label }}</span>
      </div>
    </div>

    <table class="data-table" v-if="results.length">
      <thead>
        <tr>
          <th>批次号</th>
          <th>面板编号</th>
          <th>事件总数</th>
          <th>成功率</th>
          <th>平均延迟</th>
          <th>指标状态</th>
          <th>快照版本</th>
          <th>依据指纹</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in results"
          :key="row.批次号"
          :data-testid="`batch-row-${row.批次号}`"
          :data-status="row.指标状态"
          @click="openDetail(row.批次号)"
        >
          <td>{{ row.批次号 }}</td>
          <td>{{ row.面板编号 }}</td>
          <td>{{ row.事件总数 }}</td>
          <td>{{ percent(row.成功率) }}</td>
          <td>{{ latency(row.平均延迟) }}</td>
          <td>{{ row.指标状态 }}</td>
          <td>{{ row.快照版本 }}</td>
          <td><code>{{ row.依据指纹 }}</code></td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`detail-${row.批次号}`" @click.stop="openDetail(row.批次号)">
              查看详情
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="!results.length && !loading" style="text-align:center;color:var(--muted)">请配置参数后运行分析</p>

    <section v-if="detail" class="snapshot-detail" data-testid="monitor-detail">
      <header class="snapshot-head">
        <h3>{{ detail.批次号 }} · {{ detail.采集源 }}</h3>
        <button class="link" type="button" @click="detail = null">收起</button>
      </header>
      <dl class="snapshot-grid">
        <div><dt>面板编号</dt><dd>{{ detail.面板编号 }}</dd></div>
        <div><dt>面板版本</dt><dd>{{ detail.面板版本 }}</dd></div>
        <div><dt>成功率</dt><dd>{{ percent(detail.成功率) }}</dd></div>
        <div><dt>平均延迟</dt><dd>{{ latency(detail.平均延迟) }}</dd></div>
        <div><dt>失败 / 中断 / 超时</dt><dd>{{ detail.失败量 }} / {{ detail.中断量 }} / {{ detail.超时量 }}</dd></div>
        <div><dt>恢复情况（已恢复 / 未恢复）</dt><dd>{{ detail.恢复明细?.已恢复 ?? 0 }} / {{ detail.恢复明细?.未恢复 ?? 0 }}</dd></div>
        <div><dt>指标状态</dt><dd>{{ detail.指标状态 }}（{{ detail.是否通过 ? '已通过' : '未通过' }}）</dd></div>
        <div><dt>归口口径</dt><dd>{{ detail.归口口径 }}</dd></div>
        <div><dt>窗口</dt><dd>{{ detail.窗口开始 }} ~ {{ detail.窗口结束 }}（{{ detail.窗口状态 }}）</dd></div>
        <div><dt>统计截止</dt><dd>{{ detail.统计截止 ?? '—' }}</dd></div>
        <div><dt>快照版本 / 修订次数</dt><dd>{{ detail.快照版本 }} / {{ detail.修订次数 }}</dd></div>
        <div><dt>依据指纹</dt><dd><code>{{ detail.依据指纹 }}</code></dd></div>
      </dl>
      <p class="snapshot-note">{{ detail.说明 }}</p>

      <h4>事件明细（{{ detail.事件明细?.length ?? 0 }}）</h4>
      <table class="data-table" v-if="detail.事件明细?.length">
        <thead>
          <tr><th>事件编号</th><th>结果</th><th>延迟(毫秒)</th><th>事件时间</th><th>接收时间</th><th>恢复状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="event in detail.事件明细" :key="event.事件编号">
            <td>{{ event.事件编号 }}</td>
            <td>{{ event.结果 }}</td>
            <td>{{ event.延迟毫秒 ?? '—' }}</td>
            <td>{{ event.事件时间 }}</td>
            <td>{{ event.接收时间 ?? '—' }}</td>
            <td>{{ event.恢复状态 ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-state">该批次没有采集事件：空态不能当成通过，成功率与平均延迟保持为空。</p>

      <template v-if="detail.历史依据?.length">
        <h4>历史依据（保留旧口径）</h4>
        <table class="data-table">
          <thead>
            <tr><th>版本</th><th>成功率</th><th>平均延迟</th><th>事件数</th><th>统计截止</th><th>修订原因</th></tr>
          </thead>
          <tbody>
            <tr v-for="basis in detail.历史依据" :key="basis.版本">
              <td>{{ basis.版本 }}</td>
              <td>{{ percent(basis.成功率) }}</td>
              <td>{{ latency(basis.平均延迟) }}</td>
              <td>{{ basis.事件数 }}</td>
              <td>{{ basis.统计截止 }}</td>
              <td>{{ basis.修订原因 }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </section>

    <footer class="page-foot">
      <span>共 {{ batches.length }} 个采集批次，当前展示 {{ results.length }} 个</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'

type Snapshot = Record<string, any>

const ENDPOINT = '/api/monitor'
const summaryMetrics = ref<{ label: string; value: number; trend: number }[]>([])
const results = ref<Snapshot[]>([])
const batches = ref<Snapshot[]>([])
const detail = ref<Snapshot | null>(null)
const chartData = ref<{ label: string; value: number }[]>([])
const loading = ref(false)
const errorMessage = ref('')
const params = ref<Record<string, string>>({ 批次号: '' })
const currentFingerprint = computed(() => results.value.map((row) => row.依据指纹).join(' · ') || '—')

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${Number(value).toFixed(2)}%`
}
function latency(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${Number(value).toFixed(2)} 毫秒`
}
function formatMetric(metric: { label: string; value: number }) {
  if (metric.label === '最新指标值') return `${(Number(metric.value) * 100).toFixed(1)}%`
  if (metric.label === '平均延迟') return `${Number(metric.value).toFixed(0)} ms`
  return metric.value
}
function barHeight(v: number) {
  const max = Math.max(...chartData.value.map((d) => d.value), 1)
  return Math.max(10, (v / max) * 140)
}

async function fetchSummary() {
  try {
    const res = await request(`${ENDPOINT}/summary`)
    if (!res.ok) throw new Error('汇总指标读取失败')
    summaryMetrics.value = (await res.json()).metrics || []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇总指标读取失败'
  }
}

async function fetchBatches() {
  const res = await request(`${ENDPOINT}/batches`)
  if (!res.ok) throw new Error('批次快照读取失败')
  batches.value = (await res.json()).items || []
}

function applyResults(items: Snapshot[]) {
  results.value = items
  chartData.value = items
    .filter((row) => row.成功率 !== null && row.成功率 !== undefined)
    .map((row) => ({ label: row.批次号, value: Number(row.成功率) }))
    .slice(0, 6)
}

async function runAnalysis() {
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/run`, {
      method: 'POST',
      body: JSON.stringify({ values: params.value }),
    })
    if (!res.ok) throw new Error('批次指标读取失败')
    applyResults((await res.json()).items || [])
  } catch (error) {
    results.value = []
    chartData.value = []
    errorMessage.value = error instanceof Error ? error.message : '批次指标读取失败'
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  try {
    await Promise.all([fetchSummary(), fetchBatches()])
    await runAnalysis()
    if (detail.value) {
      const res = await request(`${ENDPOINT}/batches/${encodeURIComponent(String(detail.value.批次号))}`)
      detail.value = res.ok ? await res.json() : null
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '刷新失败'
  }
}

async function openDetail(batchId: string) {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/batches/${encodeURIComponent(batchId)}`)
    if (!res.ok) throw new Error('批次详情读取失败')
    detail.value = await res.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次详情读取失败'
  }
}

async function exportRows() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/export`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'monitor.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

onMounted(async () => {
  await Promise.all([fetchSummary(), fetchBatches()])
  await runAnalysis()
})
</script>

<style scoped>
.snapshot-detail { margin-top: 16px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
.snapshot-head { display: flex; justify-content: space-between; align-items: center; }
.snapshot-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px 16px; margin: 8px 0; }
.snapshot-grid dt { font-size: 12px; color: var(--muted); }
.snapshot-grid dd { margin: 2px 0 0; font-size: 13px; }
.snapshot-note { color: var(--muted); font-size: 12px; }
.data-table tbody tr { cursor: pointer; }
</style>
