<script setup lang="ts">
import { ref } from "vue";

import { ElMessage } from "element-plus";

import { useApiQuery as query, useAction } from "../queries";

const { action, busy } = useAction();
const auth = query("auth", "/api/auth/status");
const automatic = query("automatic-auth", "/api/auth/automatic", 15000);
const loginErrors: Record<string, string> = {
  credentials_missing: "请在 credentials.yml 填写账号和密码",
  credentials_rejected: "账号或密码被拒绝；修改配置后会自动重新尝试",
  interactive_verification_required: "学校要求额外验证，请使用手动登录",
  callback_not_authenticated: "登录回调尚未建立 Canvas 会话，请使用手动登录检查",
  temporary_login_failure: "网络或验证码识别服务暂不可用，将自动重试",
  credentials_unavailable: "无法读取凭据配置，请检查文件",
};
const challenge = ref<any>(null),
  username = ref(""),
  password = ref(""),
  captcha = ref(""),
  cookies = ref("");
async function beginLogin() {
  challenge.value = await action("/api/auth/challenge");
  captcha.value = "";
}
async function login() {
  try {
    await action("/api/auth/login", {
      challenge_id: challenge.value?.challenge_id,
      username: username.value,
      password: password.value,
      captcha: captcha.value,
    });
  } finally {
    password.value = "";
    challenge.value = null;
  }
}
async function importSession() {
  try {
    const result = await action("/api/auth/import", {
      cookies: JSON.parse(cookies.value),
    });
    if (result) cookies.value = "";
  } catch {
    ElMessage.error("请填写有效的 Cookie JSON 数组");
  }
}
</script>
<template>
  <div class="auth-grid">
    <section class="panel">
      <div class="section-head">
        <h2>自动登录</h2>
        <el-tag :type="automatic.data.value?.enabled && !automatic.data.value?.blocked ? 'success' : 'info'">
          {{ !automatic.data.value?.enabled ? "未启用" : !automatic.data.value?.configured ? "待填写凭据" : automatic.data.value?.blocked ? "等待处理" : "已启用" }}
        </el-tag>
      </div>
      <p>在 <code>runtime/auth/credentials.yml</code> 填写账号密码并设置 <code>enabled: true</code>，保存后自动生效。</p>
      <p class="muted">会话失效时自动刷新或重新登录；验证码图片交给 SJTU Geek 服务识别。账号密码保存在本机 YAML 文件中，不发送给识别服务。</p>
      <p v-if="automatic.data.value?.last_error">{{ loginErrors[automatic.data.value.last_error] || "自动登录暂未完成" }}</p>
      <p v-if="automatic.data.value?.last_success" class="muted">最近完整登录：{{ new Date(automatic.data.value.last_success * 1000).toLocaleString() }}</p>
      <p v-if="automatic.data.value?.next_attempt && !automatic.data.value?.blocked" class="muted">下次允许重试：{{ new Date(automatic.data.value.next_attempt * 1000).toLocaleString() }}</p>
      <el-button type="primary" :loading="busy" :disabled="!automatic.data.value?.configured || !automatic.data.value?.enabled" @click="action('/api/auth/automatic-test')">验证全新登录</el-button>
      <el-button :loading="busy" @click="action('/api/auth/automatic-disable')">停用自动登录</el-button>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>手动登录</h2>
        <el-tag
          :type="auth.data.value?.authenticated ? 'success' : 'warning'"
          >{{
            auth.data.value?.authenticated === null ? "暂不可确认" : auth.data.value?.authenticated ? "会话有效" : "尚未登录"
          }}</el-tag
        >
      </div>
      <p class="muted">此表单仅用于单次认证，不修改 YAML 中的账号密码。</p>
      <el-button
        v-if="!challenge"
        type="primary"
        :loading="busy"
        @click="beginLogin"
        >开始登录 / 获取验证码</el-button
      ><el-form v-else label-position="top" @submit.prevent="login"
        ><el-form-item label="jAccount 用户名"
          ><el-input v-model="username" autocomplete="username" /></el-form-item
        ><el-form-item label="密码"
          ><el-input
            v-model="password"
            type="password"
            show-password
            autocomplete="current-password" /></el-form-item
        ><el-form-item v-if="challenge.captcha" label="验证码"
          ><img :src="challenge.captcha" alt="学校验证码" /><el-input
            v-model="captcha" /></el-form-item
        ><el-button type="primary" native-type="submit" :loading="busy"
          >登录</el-button
        ><el-button @click="beginLogin">刷新验证码</el-button></el-form
      ><el-divider /><el-button @click="auth.refetch()">检查会话</el-button
      ><el-button type="danger" plain @click="action('/api/auth/logout')"
        >退出并停用自动登录</el-button
      >
    </section>
    <section class="panel">
      <h2>导入现有会话</h2>
      <p class="muted">
        导入 Canvas 会话 Cookie 数组，与 CLI 的会话文件格式一致。
      </p>
      <el-input
        v-model="cookies"
        type="textarea"
        :rows="10"
        placeholder='[{"name":"…","value":"…","domain":"oc.sjtu.edu.cn","path":"/"}]'
      /><el-button
        style="margin-top: 20px"
        :disabled="!cookies"
        :loading="busy"
        @click="importSession"
        >验证并导入</el-button
      >
    </section>
  </div>
</template>
