export const statuses: Record<string, string> = {
  pending: "等待执行",
  running: "正在处理",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  needs_login: "需要登录",
  expired: "已过期",
};
export const kinds: Record<string, string> = {
  vod_asr: "回放转写",
  vod_slides: "回放 Slides",
  live: "直播监听",
  sync: "课表与视频同步",
  assignments: "作业同步",
  local_asr: "本地转写",
  local_slides: "本地 Slides",
  sample_asr: "片段转写",
  sample_slides: "片段 Slides",
};
export function tone(status: string) {
  return status === "succeeded"
    ? "success"
    : status === "failed" || status === "needs_login"
      ? "danger"
      : status === "running"
        ? "warning"
        : "info";
}
export function date(value: any) {
  if (!value) return "—";
  return new Date(
    typeof value === "number" ? value * 1000 : value,
  ).toLocaleString("zh-CN", { hour12: false });
}
