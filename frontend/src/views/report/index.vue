<template>
  <section class="page" data-module="report">
    <header class="page-head">
      <div>
        <h2>采集报告</h2>
        <p class="page-desc">
          报告直接读取监控面板的批次快照，按同一批次核对成功率和平均延迟；指标不是「正常」的批次进入待审清单。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="refresh-report" @click="reloadAll">刷新报告</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出报告清单</button>
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
        <select v-model="params['批次号']" data-testid="report-batch-select">
          <option value="">全部批次</option>
          <option v-for="batch in batches" :key="batch.批次号" :value="batch.批次号">
            {{ batch.批次号 }} · {{ batch.采集源 }}
          </option>
        </select>
      </label>
      <label>
        <span>数据来源</span>
        <input value="监控面板指标数据快照（不重新抽样）" readonly />
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
          <th>报告编号</th>
          <th>总采集量</th>
          <th>成功量</th>
          <th>失败量</th>
          <th>成功率</th>
          <th>平均延迟</th>
          <th>指标状态</th>
          <th>快照版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in results"
          :key="row.批次号"
          :data-testid="`report-row-${row.批次号}`"
          :data-status="row.指标状态"
        >
          <td>{{ row.批次号 }}</td>
          <td>{{ row.报告编号 }}</td>
          <td>{{ row.总采集量 }}</td>
          <td>{{ row.成功量 }}</td>
          <td>{{ row.失败量 }}</td>
          <td>{{ percent(row.成功率) }}</td>
          <td>{{ latency(row.平均延迟) }}</td>
          <td>{{ row.指标状态 }}（{{ row.是否通过 ? '已通过' : '未通过' }}）</td>
          <td>{{ row.快照版本 }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`regenerate-${row.批次号}`" @click="regenerate(row)">
              重新生成
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="!results.length && !loading" style="text-align:center;color:var(--muted)">请配置参数后运行分析</p>

    <section class="review-panel" data-testid="review-list">
      <header class="snapshot-head">
        <h3>待审清单（{{ reviews.length }}）</h3>
        <span style="font-size:12px;color:var(--muted)">空态与待补数不计为通过</span>
      </header>
      <table class="data-table" v-if="reviews.length">
        <thead>
          <tr><th>批次号</th><th>采集源</th><th>指标状态</th><th>成功率</th><th>平均延迟</th><th>快照版本</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in reviews" :key="item.批次号" :data-testid="`review-${item.批次号}`">
            <td>{{ item.批次号 }}</td>
            <td>{{ item.采集源 }}</td>
            <td>{{ item.指标状态 }}</td>
            <td>{{ percent(item.成功率) }}</td>
            <td>{{ latency(item.平均延迟) }}</td>
            <td>{{ item.快照版本 }}</td>
            <td>{{ item.说明 }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-state">暂无待审批次</p>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/report'
const summaryMetrics = ref<{ label: string; value: number; trend: number }[]>([])
const results = ref<Row[]>([])
const batches = ref<Row[]>([])
const reviews = ref<Row[]>([])
const chartData = ref<{ label: string; value: number }[]>([])
const loading = ref(false)
const errorMessage = ref('')
const params = ref<Record<string, string>>({ 批次号: '' })

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
  const res = await request(`${ENDPOINT}/summary`)
  if (!res.ok) throw new Error('汇总指标读取失败')
  summaryMetrics.value = (await res.json()).metrics || []
}

async function fetchReviews() {
  const res = await request(`${ENDPOINT}/reviews`)
  if (!res.ok) throw new Error('待审清单读取失败')
  reviews.value = (await res.json()).items || []
}

async function fetchBatches() {
  const res = await request(`${ENDPOINT}/batches`)
  if (!res.ok) throw new Error('批次快照读取失败')
  batches.value = (await res.json()).items || []
}

function applyResults(items: Row[]) {
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
    if (!res.ok) throw new Error('报告指标读取失败')
    applyResults((await res.json()).items || [])
  } catch (error) {
    results.value = []
    chartData.value = []
    errorMessage.value = error instanceof Error ? error.message : '报告指标读取失败'
  } finally {
    loading.value = false
  }
}

async function reloadAll() {
  try {
    await Promise.all([fetchSummary(), fetchBatches(), fetchReviews()])
    await runAnalysis()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '刷新失败'
  }
}

async function regenerate(row: Row) {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/batches/${encodeURIComponent(String(row.批次号))}/regenerate`, {
      method: 'POST',
      body: JSON.stringify({ reason: '报告页重新生成' }),
    })
    const payload = await res.json()
    if (!res.ok || !payload.ok) throw new Error(payload.message || '重新生成失败')
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重新生成失败'
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
    link.download = 'report.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.review-panel { margin-top: 16px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
.snapshot-head { display: flex; justify-content: space-between; align-items: center; }
</style>
