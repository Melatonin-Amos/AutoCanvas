<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ text: string; query: string }>();
const parts = computed(() => {
  if (!props.query.trim()) return [{ text: props.text, match: false }];
  const result: { text: string; match: boolean }[] = [];
  const needle = props.query.toLowerCase();
  let cursor = 0,
    next = 0;
  while ((next = props.text.toLowerCase().indexOf(needle, cursor)) !== -1) {
    result.push(
      { text: props.text.slice(cursor, next), match: false },
      { text: props.text.slice(next, next + needle.length), match: true },
    );
    cursor = next + needle.length;
  }
  result.push({ text: props.text.slice(cursor), match: false });
  return result;
});
</script>
<template>
  <template v-for="(part, i) in parts" :key="i"
    ><mark v-if="part.match">{{ part.text }}</mark
    ><template v-else>{{ part.text }}</template></template
  >
</template>
