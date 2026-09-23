<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "../api";
const props = defineProps<{ modelValue: string }>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const open = ref(false),
  path = ref(""),
  rows = ref<{ name: string; path: string }[]>([]),
  error = ref("");
async function browse(value: string) {
  try {
    const result = await api(
      "/api/homework/directories?path=" + encodeURIComponent(value),
    );
    path.value = result.path;
    rows.value = result.directories;
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  }
}
watch(open, (value) => {
  if (value) browse("");
});
</script>
<template>
  <div class="hw-directory">
    <el-input
      :model-value="props.modelValue"
      @update:model-value="(v: string) => emit('update:modelValue', v)"
      placeholder="课程目录/作业目录（相对工作区）"
      aria-label="作业目录"
    /><el-button @click="open = true">选择已有目录</el-button>
  </div>
  <el-dialog v-model="open" title="绑定工作区目录" width="min(640px,94vw)"
    ><p>{{ path || "工作区根目录" }}</p>
    <el-alert v-if="error" :title="error" type="error" /><el-button
      v-if="path"
      @click="browse(path.split('/').slice(0, -1).join('/'))"
      >↑ 上一级</el-button
    >
    <div class="hw-folder-list">
      <button v-for="row in rows" :key="row.path" @click="browse(row.path)">
        ▱ {{ row.name }} →
      </button>
    </div>
    <div class="dialog-actions">
      <el-button
        :disabled="!path"
        type="primary"
        @click="
          emit('update:modelValue', path);
          open = false;
        "
        >使用此目录</el-button
      >
    </div></el-dialog
  >
</template>
