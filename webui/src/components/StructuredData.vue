<script setup lang="ts">
import { computed } from "vue";
import { date } from "../format";
import { timecode } from "../workspace";
const props = withDefaults(defineProps<{ value: unknown; depth?: number }>(), {
  depth: 0,
});
const labels: Record<string, string> = {
  id: "编号",
  name: "名称",
  title: "标题",
  description: "正文",
  course_id: "课程编号",
  lecture_id: "课次编号",
  start: "开始时间",
  end: "结束时间",
  text: "文字内容",
  status: "状态",
  succeeded: "已完成",
  failed: "失败",
  updated_at: "更新时间",
  due_at: "截止时间",
  points_possible: "分值",
  attachments: "附件",
  image: "画面文件",
  slide_number: "画面序号",
  time_seconds: "首次出现",
  time_hhmmss: "首次出现时间",
  last_seen_seconds: "最后出现",
  last_seen_hhmmss: "最后出现时间",
  match_count: "匹配画面数",
  matched_sample_frames: "匹配帧",
  decision: "画面选择依据",
  reason: "原因",
  phash: "画面指纹",
  dhash: "差异指纹",
  laplacian_variance: "清晰度指标",
  video_id: "视频标识",
  sha256: "文件校验",
  path: "文件位置",
  type: "事件类型",
  at: "发生时间",
  keyword: "关键词",
  error: "错误类型",
  code: "响应代码",
  stage: "处理阶段",
  resume_after: "恢复位置",
  offset: "时间偏移",
  queued_chunks: "待处理片段数",
  html_url: "原始页面",
  monitor_start: "开始监听",
  monitor_stop: "结束监听",
  connected: "直播已连接",
  connection_interrupted: "连接中断",
  audio_gap: "音频缺口",
  authentication_required: "等待认证",
  first_frame: "首帧",
  changed: "画面发生变化",
  queue_full: "处理队列已满",
};
const entries = computed(() =>
  props.value && typeof props.value === "object" && !Array.isArray(props.value)
    ? Object.entries(props.value)
    : [],
);
const nested = (v: unknown) => v !== null && typeof v === "object";
function display(key: string, v: unknown): string {
  if (v == null) return "未提供";
  if (typeof v === "boolean") return v ? "是" : "否";
  if (["updated_at", "due_at", "at"].includes(key) && v) return date(v);
  if (
    [
      "start",
      "end",
      "time_seconds",
      "last_seen_seconds",
      "offset",
      "resume_after",
    ].includes(key) &&
    typeof v === "number"
  )
    return timecode(v);
  return labels[String(v)] || String(v);
}
</script>
<template>
  <div v-if="depth > 5" class="muted">更多嵌套数据请下载原文件查看。</div>
  <div v-else-if="Array.isArray(value)" class="structured-list">
    <details
      v-for="(item, i) in value.slice(0, 100)"
      :key="i"
      :open="value.length < 4"
    >
      <summary>
        {{
          item && typeof item === "object"
            ? item.name ||
              (item.slide_number
                ? "画面 " + item.slide_number
                : item.type
                  ? labels[item.type] || item.type
                  : "记录 " + (i + 1))
            : "项目 " + (i + 1)
        }}
      </summary>
      <StructuredData :value="item" :depth="depth + 1" />
    </details>
    <p v-if="value.length > 100" class="muted">
      共 {{ value.length }} 条，展示前 100 条。完整内容可下载查看。
    </p>
    <p v-if="!value.length" class="muted">暂无记录。</p>
  </div>
  <dl v-else-if="entries.length" class="readable-fields">
    <template v-for="[key, item] in entries" :key="key"
      ><dt>{{ labels[key] || key.replaceAll("_", " ") }}</dt>
      <dd>
        <details v-if="nested(item)">
          <summary>
            {{ Array.isArray(item) ? item.length + " 项内容" : "展开详情" }}
          </summary>
          <StructuredData :value="item" :depth="depth + 1" />
        </details>
        <span v-else>{{ display(key, item) }}</span>
      </dd></template
    >
  </dl>
  <p v-else>
    {{
      value && typeof value === "object" ? "暂无详细内容。" : display("", value)
    }}
  </p>
</template>
