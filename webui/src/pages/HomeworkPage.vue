<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ElMessage, ElTabs, ElTabPane, vLoading } from "element-plus";
import { api } from "../api";
import { date } from "../format";
interface Session { id: string; name: string; model: string; native_id?: string; status: string; }
interface Run { id: string; prompt: string; result: string; status: string; error: string; }
interface Event { seq: number; run_id: string; kind: string; body: any; }
const cache = useQueryClient();
const tab = ref("codex"), selectedId = ref(""), prompt = ref(""), model = ref(""), directory = ref(""), busy = ref(false);
const root = "/api/codex";
const config = useQuery({ queryKey: ["codex", "config"], queryFn: () => api(root + "/config"), retry: false });
watch(config.data, (value) => { if (value) directory.value = value.workspace; }, { immediate: true });
const sessions = useQuery<Session[]>({ queryKey: ["codex", "sessions"], queryFn: () => api(root + "/sessions"), refetchInterval: 3000, retry: false });
const selected = computed(() => sessions.data.value?.find(s => s.id === selectedId.value));
watch(sessions.data, rows => { if (!selectedId.value && rows?.length) selectedId.value = rows[0]!.id; });
watch(() => [selected.value?.id, selected.value?.model], () => { model.value = selected.value?.model || ""; });
const runs = useQuery<Run[]>({ queryKey: computed(() => ["codex", "turns", selectedId.value]), queryFn: () => api(root + "/sessions/" + selectedId.value + "/turns"), enabled: computed(() => !!selectedId.value), refetchInterval: 1500 });
const history = useQuery<{role: string; text: string}[]>({ queryKey: computed(() => ["codex", "history", selectedId.value]), queryFn: () => api(root + "/sessions/" + selectedId.value + "/history"), enabled: computed(() => !!selectedId.value) });
const events = ref<Event[]>([]);
watch(selectedId, () => { events.value = []; prompt.value = ""; });
const eventQuery = useQuery<Event[]>({ queryKey: computed(() => ["codex", "events", selectedId.value]), queryFn: async () => {
  const id = selectedId.value;
  const rows: Event[] = await api(root + "/sessions/" + id + "/events?after=" + (events.value.at(-1)?.seq || 0));
  if (selectedId.value === id) events.value.push(...rows.filter(row => !events.value.some(e => e.seq === row.seq)));
  return rows;
}, enabled: computed(() => !!selectedId.value), refetchInterval: 1000 });
const running = computed(() => selected.value?.status === "running" || runs.data.value?.some(r => ["queued", "running"].includes(r.status)));
const assignments = useQuery<any[]>({ queryKey: ["assignments"], queryFn: () => api("/api/assignments"), retry: false });
const courses = useQuery<any[]>({ queryKey: ["courses"], queryFn: () => api("/api/courses"), retry: false });
const search = ref("");
function courseName(id: unknown) { return courses.data.value?.find(c => String(c.id) === String(id))?.name || String(id); }
const visible = computed(() => (assignments.data.value || []).filter(a => `${a.name} ${courseName(a.course_id)}`.toLowerCase().includes(search.value.toLowerCase())));
const status: Record<string,string> = { queued: "等待执行", running: "执行中", succeeded: "已完成", failed: "执行失败", interrupted: "已中断", cancelled: "已停止" };
async function act(path: string, body: unknown = {}, method = "POST") {
  busy.value = true;
  try { const result = await api(root + path, method, body); await cache.invalidateQueries({queryKey: ["codex"]}); return result; }
  catch (error) { ElMessage.error((error as Error).message); }
  finally { busy.value = false; }
}
async function create() { const row = await act("/sessions", { model: model.value }); if (row) selectedId.value = row.id; }
async function send() {
  const text = prompt.value.trim(), id = selectedId.value;
  if (!text || !id) return;
  const row = await act("/sessions/" + id + "/turns", { prompt: text, request_id: crypto.randomUUID() });
  if (row && selectedId.value === id) prompt.value = "";
}
async function saveModel() { if (selected.value && await act("/sessions/" + selected.value.id, { model: model.value }, "PATCH")) ElMessage.success("模型已更新，下次发送时生效"); }
async function saveDirectory() { if (await act("/config", { workspace: directory.value }, "PATCH")) ElMessage.success("工作目录已更新"); }
function messages(run: Run) { return events.value.filter(e => e.run_id === run.id && e.kind === "message").map(e => e.body.text).join("\n\n") || run.result; }
function output(run: Run) { return events.value.filter(e => e.run_id === run.id && ["activity", "diagnostic", "error"].includes(e.kind)).map(e => e.body.text || e.body.aggregated_output || e.body.command || e.body.message || "").filter(Boolean).join("\n"); }
function safeLink(url: string) { try { const u = new URL(url); return ["http:","https:"].includes(u.protocol) ? u.href : undefined; } catch { return undefined; } }
</script>

<template>
  <el-tabs v-model="tab">
    <el-tab-pane label="作业列表" name="assignments">
      <div class="simple-toolbar"><el-input v-model="search" placeholder="搜索作业或课程" clearable /><el-button @click="assignments.refetch()">刷新列表</el-button></div>
      <el-alert v-if="assignments.isError.value" :title="assignments.error.value?.message" type="error" :closable="false" />
      <el-table v-else :data="visible" v-loading="assignments.isLoading.value" empty-text="暂无作业">
        <el-table-column label="作业" min-width="240"><template #default="{ row }"><a v-if="safeLink(row.html_url)" :href="safeLink(row.html_url)" target="_blank" rel="noopener noreferrer">{{ row.name }} ↗</a><span v-else>{{ row.name }}</span></template></el-table-column>
        <el-table-column label="课程" min-width="180"><template #default="{ row }">{{ courseName(row.course_id) }}</template></el-table-column>
        <el-table-column label="截止时间" min-width="180"><template #default="{ row }">{{ row.due_at ? date(row.due_at) : '未设置' }}</template></el-table-column>
      </el-table>
    </el-tab-pane>
    <el-tab-pane label="Codex 会话" name="codex">
      <div class="simple-toolbar"><label for="codex-directory">工作目录</label><el-input id="codex-directory" v-model="directory" placeholder="绝对目录路径" :disabled="busy" /><el-button :disabled="busy || directory === config.data.value?.workspace" @click="saveDirectory">保存</el-button></div>
      <p class="directory-note">所有会话直接在此目录执行，沿用目录中的 AGENTS.md。</p>
      <el-alert v-if="config.isError.value || sessions.isError.value" :title="config.error.value?.message || sessions.error.value?.message || '暂时无法读取会话'" type="error" :closable="false" />
      <div class="codex-layout">
        <section class="codex-sessions">
          <el-button type="primary" :loading="busy" @click="create">新建会话</el-button>
          <p v-if="sessions.isLoading.value" class="directory-note">正在读取会话…</p>
          <p v-else-if="!sessions.data.value?.length" class="directory-note">暂无会话</p>
          <button v-for="s in sessions.data.value" :key="s.id" class="session-row" :class="{ selected: s.id === selectedId }" @click="selectedId = s.id"><strong>{{ s.name }}</strong><small>{{ s.status === 'running' ? '执行中 · ' : '' }}{{ s.model || '默认模型' }}</small></button>
        </section>
        <section class="codex-conversation">
          <template v-if="selected">
            <div class="simple-toolbar"><strong class="conversation-title">{{ selected.name }}</strong><el-select v-model="model" filterable allow-create default-first-option placeholder="默认模型" :disabled="!!running || busy" aria-label="会话模型"><el-option label="默认模型" value="" /><el-option v-for="m in config.data.value?.models || []" :key="m" :label="m" :value="m" /></el-select><el-button :disabled="!!running || busy || model === selected.model" @click="saveModel">切换模型</el-button></div>
            <small v-if="selected.native_id" class="directory-note">{{ selected.native_id }}</small>
            <el-alert v-if="runs.isError.value || history.isError.value || eventQuery.isError.value" title="部分会话记录读取失败，请刷新页面重试" type="error" :closable="false" />
            <div class="conversation-messages" aria-live="polite">
              <article v-for="(message, i) in history.data.value" :key="'history-' + i" :class="['conversation-message', message.role]"><small>{{ message.role === 'user' ? '你' : 'Codex' }}</small><pre>{{ message.text }}</pre></article>
              <template v-for="run in runs.data.value" :key="run.id">
                <article class="conversation-message user"><small>你</small><pre>{{ run.prompt }}</pre></article>
                <article class="conversation-message"><small>Codex · {{ status[run.status] || run.status }}</small><pre v-if="messages(run)">{{ messages(run) }}</pre><p v-else-if="run.status === 'running'">正在执行…</p><p v-if="run.error" class="run-error">{{ run.error }}</p><details v-if="output(run)"><summary>执行输出</summary><pre>{{ output(run) }}</pre></details></article>
              </template>
              <p v-if="!history.data.value?.length && !runs.data.value?.length" class="directory-note">输入消息开始会话。</p>
            </div>
            <el-input v-model="prompt" type="textarea" :rows="4" placeholder="发给 Codex 的消息" :disabled="busy" @keydown.ctrl.enter.prevent="!running && !busy && send()" @keydown.meta.enter.prevent="!running && !busy && send()" />
            <div class="send-actions"><el-button v-if="running" :loading="busy" @click="act('/sessions/' + selectedId + '/stop')">停止执行</el-button><el-button type="primary" :disabled="!prompt.trim() || !!running" :loading="busy" @click="send">发送</el-button></div>
          </template>
          <el-empty v-else description="选择已有会话，或新建会话" />
        </section>
      </div>
    </el-tab-pane>
  </el-tabs>
</template>

<style scoped>
.simple-toolbar { display:flex; align-items:center; gap:12px; margin-bottom:12px; }
.simple-toolbar label { flex:none; }
.simple-toolbar > .el-input { flex:1; }
.directory-note { color:#738279; font-size:12px; overflow-wrap:anywhere; }
.codex-layout { display:grid; grid-template-columns:240px minmax(0,1fr); border:1px solid #dfe7df; border-radius:12px; background:#fff; margin-top:20px; overflow:hidden; }
.codex-sessions { padding:16px; border-right:1px solid #dfe7df; max-height:80vh; overflow:auto; }
.codex-sessions > .el-button { width:100%; margin-bottom:16px; }
.session-row { display:block; width:100%; text-align:left; border:0; background:transparent; padding:12px; border-radius:8px; cursor:pointer; color:inherit; margin-bottom:6px; }
.session-row:hover, .session-row.selected { background:#edf3ec; }
.session-row strong { display:block; font-size:13px; overflow-wrap:anywhere; font-weight:500; }
.session-row small { display:block; margin-top:6px; color:#738279; }
.codex-conversation { padding:24px; min-width:0; }
.conversation-title { flex:1; overflow-wrap:anywhere; }
.simple-toolbar .el-select { width:200px; flex:none; }
.conversation-messages { max-height:60vh; overflow:auto; padding:16px 0; margin:12px 0; }
.conversation-message { padding:16px; margin-bottom:12px; background:#f7f9f6; border-radius:10px; }
.conversation-message.user { background:#edf3ec; }
.conversation-message small { color:#6a7b72; }
.conversation-message pre { font-family:inherit; white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.7; margin:8px 0 0; }
.conversation-message details { margin-top:12px; font-size:12px; }
.conversation-message details pre { font-family:monospace; }
.run-error { color:#b43f34; }
.send-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:12px; }
@media(max-width:900px) { .codex-layout { grid-template-columns:1fr; } .codex-sessions { border-right:0; border-bottom:1px solid #dfe7df; max-height:220px; } .codex-conversation { padding:16px; } .simple-toolbar { flex-wrap:wrap; } }
</style>
