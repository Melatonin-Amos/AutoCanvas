<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import { useApiQuery as query, useAction } from "../queries";
import { date, statuses, kinds, tone } from "../format";
import ExecutionDetail from "../components/ExecutionDetail.vue";
const router = useRouter();
const { action } = useAction();
const courses = query("courses", "/api/courses");
const courseRows = computed<any[]>(() => courses.data.value || []);
function courseName(id: string) {
  return courseRows.value.find((r) => r.id === id)?.name || id;
}
const executions = query("executions", "/api/executions");
const runs = computed<any[]>(() => executions.data.value || []);
const health = query("health", "/health", 10000),
  lectures = query("lectures", "/api/lectures");
const executionDetail = ref<any>(null);
</script>
<template>
  <div class="stats">
    <div>
      <span>已同步课程</span
      ><strong>{{ courseRows.length }}<small>门课程</small></strong>
    </div>
    <div>
      <span>视频记录</span
      ><strong
        >{{ (lectures.data.value || []).length }}<small>条记录</small></strong
      >
    </div>
    <div>
      <span>正在处理</span
      ><strong
        >{{ runs.filter((r) => r.status === "running").length
        }}<small>项执行</small></strong
      >
    </div>
    <div>
      <span>已完成</span
      ><strong
        >{{ runs.filter((r) => r.status === "succeeded").length
        }}<small>项执行</small></strong
      >
    </div>
  </div>
  <div class="overview-grid">
    <section class="panel">
      <div class="section-head">
        <h2>最近执行</h2>
        <RouterLink to="/executions">查看全部 ↗</RouterLink>
      </div>
      <div v-if="!runs.length" class="empty">
        还没有执行记录。同步课程后，系统会自动安排处理。
      </div>
      <div
        v-for="run in runs.slice(0, 6)"
        :key="run.id"
        class="run-row"
        @click="executionDetail = run.id"
      >
        <span class="run-symbol">{{
          run.kind.includes("slides") ? "▧" : "≋"
        }}</span>
        <div>
          <b>{{ kinds[run.kind] || run.kind }}</b
          ><small
            >{{ courseName(run.course_id) }} · {{ date(run.updated) }}</small
          >
        </div>
        <el-tag :type="tone(run.status)">{{ statuses[run.status] }}</el-tag>
      </div>
    </section>
    <section class="automation-card">
      <div class="eyebrow">AUTOMATION</div>
      <h2>让课程持续同步</h2>
      <p>定时更新课程、处理回放，在开课前启动直播监听。</p>
      <div class="automation-state">
        <span>{{
          !health.data.value?.scheduler_running
            ? "调度未启动"
            : health.data.value?.automation?.paused
              ? "自动化已暂停"
              : "自动化已启用"
        }}</span
        ><el-switch
          :model-value="
            health.data.value?.scheduler_running &&
            !health.data.value?.automation?.paused
          "
          @change="(v: any) => action('/api/automation', { paused: !v })"
        />
      </div>
      <RouterLink to="/settings">调整自动化设置 ↗</RouterLink>
      <div class="orb"></div>
    </section>
  </div>
  <section class="panel">
    <div class="section-head">
      <h2>我的课程</h2>
      <RouterLink to="/files">浏览课程产物 ↗</RouterLink>
    </div>
    <div class="course-grid">
      <div
        v-for="c in courseRows.slice(0, 6)"
        :key="c.id"
        class="course-tile"
        @click="router.push({ path: '/files', query: { course: c.id } })"
      >
        <small>COURSE / {{ c.id }}</small>
        <h3>{{ c.name }}</h3>
        <span>{{ c.active ? "参与自动化" : "已停用" }} <b>↗</b></span>
      </div>
      <el-empty
        v-if="!courseRows.length"
        description="同步课表后，课程会显示在这里"
      />
    </div>
  </section>
  <ExecutionDetail :id="executionDetail" @close="executionDetail = null" />
</template>
