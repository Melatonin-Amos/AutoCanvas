export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers:
      body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  if (response.status === 401 && response.headers.get("X-Dashboard-Auth") === "required") {
    window.location.replace("/_dashboard/login");
    throw new Error("访问会话已过期，请重新登录");
  }
  if (!response.ok) {
    const text = await response.text();
    let message = `请求失败 (${response.status})`;
    try {
      message = JSON.parse(text).error || message;
    } catch {
      /* HTML proxy errors are not displayed */
    }
    throw new Error(message);
  }
  return response.json();
}
export const fileUrl = (path: string, download = false) =>
  "/api/file?path=" +
  encodeURIComponent(path) +
  (download ? "&download=1" : "");
