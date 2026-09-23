import { computed } from "vue";
import { useApiQuery } from "./queries";
import type { Course, Lecture, Run } from "./workspace";
export function useWorkspace() {
  const courseQuery = useApiQuery("courses", "/api/courses");
  const lectureQuery = useApiQuery("lectures", "/api/lectures");
  const runQuery = useApiQuery("executions", "/api/executions");
  const courses = computed<Course[]>(() => courseQuery.data.value || []);
  const lectures = computed<Lecture[]>(() => lectureQuery.data.value || []);
  const runs = computed<Run[]>(() => runQuery.data.value || []);
  const courseName = (id: string) =>
    courses.value.find((c) => c.id === id)?.name ||
    (id === "*" ? "全部课程" : id === "local" ? "历史本地资料" : `课程 ${id}`);
  return {
    courses,
    lectures,
    runs,
    courseName,
    courseQuery,
    lectureQuery,
    runQuery,
  };
}
