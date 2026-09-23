<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useWorkspace } from "../useWorkspace";
import { useAction } from "../queries";
import { date, kinds, statuses, tone } from "../format";
import { isActive, findLecture, lectureTitle, errorAdvice } from "../workspace";
import ExecutionDetail from "../components/ExecutionDetail.vue";
const { courses, lectures, runs, courseName, runQuery } = useWorkspace();
const { action, busy } = useAction();
const route = useRoute(),
  router = useRouter();
const status = ref(String(route.query.status || "all")),
  course = ref(String(route.query.course || "")),
  search = ref(""),
  kind = ref(""),
  page = ref(1),
  detailId = ref<string | null>(null);
const history = computed(() =>
  runs.value.filter((r) => !isActive(r)).sort((a, b) => b.updated - a.updated),
);
const filtered = computed(() =>
  history.value.filter(
    (r) =>
      (status.value === "all" || r.status === status.value) &&
      (!course.value || r.course_id === course.value) &&
      (!kind.value || r.kind === kind.value) &&
      `${courseName(r.course_id)} ${lectureTitle(findLecture(r, lectures.value))} ${kinds[r.kind]}`
        .toLowerCase()
        .includes(search.value.toLowerCase()),
  ),
);
const pages = computed(() =>
  Math.max(1, Math.ceil(filtered.value.length / 12)),
);
const visible = computed(() =>
  filtered.value.slice((page.value - 1) * 12, page.value * 12),
);
watch([status, course, kind, search], () => (page.value = 1));
watch(pages, (n) => (page.value = Math.min(page.value, n)));
watch(
  () => route.query.status,
  (value) => (status.value = String(value || "all")),
);
</script>
<template>
  <div class="workspace-toolbar">
    <div class="segmented" aria-label="历史状态">
      <button
        v-for="[key, label] in [
          ['all', '全部记录'],
          ['succeeded', '已完成'],
          ['failed', '失败'],
          ['cancelled', '已取消'],
          ['expired', '已过期'],
        ]"
        :key="key"
        :class="{ selected: status === key }"
        :aria-pressed="status === key"
        @click="status = key"
      >
        {{ label
        }}<small>{{
          key === "all"
            ? history.length
            : history.filter((r) => r.status === key).length
        }}</small>
      </button>
    </div>
    <RouterLink to="/courses">待执行与执行中 →</RouterLink>
  </div>
  <div class="toolbar">
    <el-input
      v-model="search"
      placeholder="搜索课程、课次或处理内容"
      aria-label="搜索执行记录"
      clearable
    /><el-select
      v-model="course"
      clearable
      placeholder="全部课程"
      aria-label="筛选历史课程"
      style="width: 240px"
      ><el-option
        v-for="c in courses"
        :key="c.id"
        :value="c.id"
        :label="c.name" /></el-select
    ><el-select
      v-model="kind"
      clearable
      placeholder="全部处理类型"
      aria-label="筛选处理类型"
      style="width: 180px"
      ><el-option
        v-for="(label, key) in kinds"
        :key="key"
        :value="key"
        :label="label"
    /></el-select>
  </div>
  <section class="panel history-panel">
    <div class="section-head">
      <h2>
        处理历史 <small class="count-label">{{ filtered.length }} 条</small>
      </h2>
      <span class="muted">按最近更新排序</span>
    </div>
    <div v-if="runQuery.isPending.value" class="empty-state">
      正在读取执行记录…
    </div>
    <div v-else-if="runQuery.isError.value" class="empty-state">
      <h3>暂时无法加载记录</h3>
      <el-button @click="runQuery.refetch()">重试</el-button>
    </div>
    <div v-else-if="!visible.length" class="empty-state">
      <div class="empty-symbol">≋</div>
      <h3>没有符合条件的历史记录</h3>
      <p>任务结束后会显示在这里。</p>
      <el-button
        @click="
          status = 'all';
          course = '';
          kind = '';
          search = '';
        "
        >清除筛选</el-button
      >
    </div>
    <article v-for="run in visible" :key="run.id" class="history-entry">
      <span class="history-icon" :class="run.status">{{
        run.status === "succeeded" ? "✓" : run.status === "failed" ? "!" : "—"
      }}</span>
      <div class="history-body">
        <div class="history-title">
          <h3>{{ kinds[run.kind] || "课程处理" }}</h3>
          <el-tag :type="tone(run.status)">{{ statuses[run.status] }}</el-tag>
        </div>
        <p>
          {{ courseName(run.course_id)
          }}<span v-if="run.lecture_id">
            · {{ lectureTitle(findLecture(run, lectures)) }}</span
          >
        </p>
        <small
          >{{ date(run.updated)
          }}<span v-if="findLecture(run, lectures)?.begin">
            · 课次 {{ date(findLecture(run, lectures)?.begin) }}</span
          ></small
        >
        <p v-if="run.status === 'failed'" class="failure-hint">
          {{ errorAdvice(run.error) }}
        </p>
      </div>
      <div class="history-actions">
        <el-button
          v-if="run.artifact"
          type="primary"
          plain
          @click="
            router.push({
              path: '/files',
              query: {
                course: run.course_id,
                lecture: run.lecture_id || undefined,
                section: run.kind === 'assignments' ? 'assignments' : undefined,
                sample: run.kind.startsWith('sample_') ? '1' : undefined,
              },
            })
          "
          >查看产物</el-button
        ><el-button
          v-if="['failed', 'cancelled'].includes(run.status)"
          :loading="busy"
          @click="action('/api/executions/' + run.id + '/retry')"
          >重新执行</el-button
        ><el-button link @click="detailId = run.id">详情</el-button>
      </div>
    </article>
    <div v-if="filtered.length > 12" class="pagination-bar">
      <span>第 {{ page }} / {{ pages }} 页</span>
      <div>
        <el-button :disabled="page === 1" @click="page--">上一页</el-button
        ><el-button :disabled="page === pages" @click="page++"
          >下一页</el-button
        >
      </div>
    </div>
  </section>
  <ExecutionDetail :id="detailId" @close="detailId = null" />
</template>
