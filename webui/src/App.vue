<script setup lang="ts">
import MaterialIcon from "./components/MaterialIcon.vue";
import { computed, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useQueryClient } from "@tanstack/vue-query";
import { useApiQuery as query, useAction } from "./queries";
const route = useRoute(),
  router = useRouter(),
  cache = useQueryClient();
const { action, busy } = useAction();
const nav = [
  ["/", "概览", "dashboard"],
  ["/courses", "待执行课程", "calendar"],
  ["/executions", "执行记录", "history"],
  ["/files", "产物浏览", "folder"],
  ["/homework", "作业", "assignment"],
  ["/classroom-events", "点名与二维码", "qr"],
  ["/settings", "全部设置", "settings"],
  ["/auth", "登录与鉴权", "lock"],
  ["/logs", "服务日志", "logs"],
] as const;
const title = computed(
  () => nav.find((x) => x[0] === route.path)?.[1] || "概览",
);
const descriptions: Record<string, string> = {
  "/": "课程自动处理，学习资料随时可读。",
  "/courses": "管理待办、跟进处理进度，让每一节课都有安排。",
  "/executions": "回顾已结束的处理，查看结果，处理未完成的任务。",
  "/files": "按课程回顾课堂画面、阅读转写，找到你需要的资料。",
  "/classroom-events": "按课程回顾点名线索与二维码出现记录。",
  "/homework": "查看作业列表，使用 Codex 会话。",
  "/settings": "管理服务和处理参数，查看每项设置的生效时机。",
  "/auth": "管理登录状态，让课程同步持续运行。",
  "/logs": "查看服务运行情况，定位异常。",
};
const health = query("health", "/health", 10000),
  auth = query("auth", "/api/auth/status");
const connected = ref(false);
let stream: EventSource | undefined;
let reconnect: ReturnType<typeof setTimeout> | undefined;
function connect() {
  stream = new EventSource("/api/events");
  stream.onopen = () => {
    connected.value = true;
  };
  stream.onerror = () => {
    connected.value = false;
    stream?.close();
    reconnect = setTimeout(connect, 3000);
  };
  stream.addEventListener("state", (event) => {
    connected.value = true;
    const state = JSON.parse((event as MessageEvent).data);
    const previous = cache.getQueryData<any[]>(["executions"]);
    cache.setQueryData(["executions"], state.executions);
    if (JSON.stringify(previous) !== JSON.stringify(state.executions)) {
      for (const key of [
        "files",
        "courses",
        "lectures",
        "assignments",
        "health",
      ])
        cache.invalidateQueries({ queryKey: [key] });
    }
  });
}
connect();
onUnmounted(() => {
  clearTimeout(reconnect);
  stream?.close();
});
</script>
<template>
  <div class="layout">
    <aside>
      <a class="brand" href="/" @click.prevent="router.push('/')"
        ><span class="brand-mark">A<span>·</span></span
        ><span>AutoCanvas<small>PERSONAL LEARNING SPACE</small></span></a
      >
      <div class="nav-label">工作空间 <span>V2</span></div>
      <nav>
        <RouterLink v-for="item in nav" :key="item[0]" :to="item[0]"
          ><MaterialIcon class="nav-icon" :name="item[2]" />{{ item[1] }}</RouterLink
        >
      </nav>
      <div class="sidebar-bottom">
        <span :class="['dot', connected ? 'online' : '']"></span
        >{{ connected ? "实时连接已建立" : "正在连接服务…"
        }}<small>独立界面 · 本地运行</small>
      </div>
    </aside>
    <main>
      <header>
        <div class="breadcrumb">工作空间 <span>/</span> {{ title }}</div>
        <div class="header-actions">
          <el-tag
            :type="health.data.value?.status === 'ok' ? 'success' : 'warning'"
            >{{
              health.data.value?.status === "ok"
                ? "服务正常"
                : health.data.value?.status || "连接中"
            }}</el-tag
          ><RouterLink to="/auth">{{
            auth.data.value?.authenticated ? "已登录 Canvas" : "登录 Canvas ↗"
          }}</RouterLink>
          <form action="/_dashboard/logout" method="post"><button type="submit" class="text-button">退出登录</button></form>
        </div>
      </header>
      <div class="content">
        <div
          v-if="health.isError.value && route.path !== '/homework'"
          class="error-banner"
        >
          无法连接 API。请启动 AutoCanvas 服务，并检查 WebUI
          的代理地址。<el-button @click="cache.invalidateQueries()"
            >重试</el-button
          >
        </div>
        <div class="page-heading">
          <div>
            <div class="eyebrow">
              AUTOCANVAS /
              {{
                String(nav.findIndex((n) => n[0] === route.path) + 1).padStart(
                  2,
                  "0",
                )
              }}
            </div>
            <h1>{{ title }}</h1>
            <p>
              {{ descriptions[route.path] || "" }}
            </p>
          </div>
          <el-button
            v-if="route.path === '/' || route.path === '/courses'"
            type="primary"
            :loading="busy"
            @click="action('/api/sync')"
            >↻ 同步课表与课程</el-button
          >
        </div>
        <RouterView />
        <footer>
          AutoCanvas <span>V2</span><span>让学习资料有序发生。</span>
        </footer>
      </div>
    </main>
  </div>
</template>
