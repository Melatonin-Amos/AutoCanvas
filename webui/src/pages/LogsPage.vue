<script setup lang="ts">
import { useApiQuery as query } from "../queries";

const logs = query("logs", "/api/logs", 5000);
</script>
<template>
  <section class="panel">
    <div class="section-head">
      <h2>服务日志</h2>
      <span class="muted">每 5 秒刷新 · 最近 64 KB</span>
    </div>
    <pre class="log-view">{{ logs.data.value?.text || "暂无日志。" }}</pre>
  </section>
</template>
