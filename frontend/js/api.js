/**
 * REST-Client des Frontends (Spezifikation D).
 *
 * Basis ist die eigene Origin: Das Backend liefert das Frontend aus (D10),
 * daher sind alle Pfade relativ und es gibt keine CORS-/Auth-Konfiguration.
 * Fehler werden auf die einheitliche Form `{code, message, retryable}` (D.0)
 * normalisiert und als `ApiError` geworfen.
 */

export class ApiError extends Error {
  constructor({ code, message, retryable = false, status = 0 }) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.message = message;
    this.retryable = retryable;
    this.status = status;
  }
}

async function request(path, { method = "GET", body, signal, headers = {} } = {}) {
  const init = { method, headers: { Accept: "application/json", ...headers }, signal };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, init);
  } catch (cause) {
    throw new ApiError({
      code: "NETWORK",
      message: `Netzwerkfehler bei ${method} ${path}`,
      retryable: true,
    });
  }

  if (response.status === 204) {
    return null;
  }

  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const error = (payload && payload.error) || {};
    throw new ApiError({
      code: error.code || `HTTP_${response.status}`,
      message: error.message || response.statusText || `HTTP ${response.status}`,
      retryable: Boolean(error.retryable),
      status: response.status,
    });
  }
  return payload;
}

export const api = {
  request,
  getConfig: () => request("/api/config"),
  getWidgets: () => request("/api/widgets"),
  getWidget: (id) => request(`/api/widgets/${encodeURIComponent(id)}`),
  patchWidget: (id, patch) =>
    request(`/api/widgets/${encodeURIComponent(id)}`, { method: "PATCH", body: patch }),
  getLayout: (tab) => request(`/api/layout?tab=${encodeURIComponent(tab)}`),
  createLayout: (payload) => request("/api/layout", { method: "POST", body: payload }),
  updateLayout: (instanceId, patch) =>
    request(`/api/layout/${encodeURIComponent(instanceId)}`, { method: "PUT", body: patch }),
  getTasks: (status = "open", limit = 100) =>
    request(`/api/tasks?status=${encodeURIComponent(status)}&limit=${limit}`),
  patchTask: (id, patch) =>
    request(`/api/tasks/${encodeURIComponent(id)}`, { method: "PATCH", body: patch }),
  getState: (key) => request(`/api/state/${encodeURIComponent(key)}`),
  putState: (key, value) =>
    request(`/api/state/${encodeURIComponent(key)}`, { method: "PUT", body: { value } }),
};
