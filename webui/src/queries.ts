import { ref, watch } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ElMessage } from "element-plus";
import { api } from "./api";
export function useApiQuery(
  key: string,
  path: string,
  interval: number | false = false,
) {
  const result = useQuery({
    queryKey: [key],
    queryFn: () => api(path),
    refetchInterval: interval || (key === "executions" ? 5000 : false),
  });
  watch(result.error, (error) => {
    if (error && key !== "health") ElMessage.error(error.message);
  });
  return result;
}
export function useAction() {
  const busy = ref(false),
    cache = useQueryClient();
  async function action(path: string, body: unknown = {}, method = "POST") {
    busy.value = true;
    try {
      const result = await api(path, method, body);
      await cache.invalidateQueries();
      ElMessage.success(result.execution_id ? "已加入执行队列" : "操作已完成");
      return result;
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }
  return { action, busy };
}
