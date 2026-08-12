const state = {
  health: null,
  assets: [],
  projects: [],
  selectedAssets: new Set(),
  project: null,
  plan: null,
  quality: "draft",
  jobTimer: null,
  jobClockTimer: null,
  activeJobId: null,
  jobStartedAt: null,
  jobEstimatedSeconds: 0,
};

const ASSET_ROLE_LABELS = {
  reference: "普通参考",
  character: "角色",
  location: "场景",
  prop: "道具",
  style: "画风",
  start_frame: "首帧",
  audio: "声音",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_) {
      // Keep the HTTP fallback message.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, timeout = 3200) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), timeout);
}

function formatTime(seconds) {
  const rounded = Math.round(seconds);
  if (rounded < 60) return `${rounded} 秒`;
  const minutes = Math.floor(rounded / 60);
  const rest = rounded % 60;
  if (minutes < 60) return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const minuteRest = minutes % 60;
  return `${hours} 小时 ${minuteRest} 分`;
}

function estimateProjectSeconds(project) {
  if (!project?.shots?.length) return 0;
  let total = 0;
  project.shots.forEach((shot, index) => {
    total += 381.924 * (shot.inference_steps / 50) * (shot.duration_seconds / 15) + 8;
    total += 2;
    if (index > 0 && !shot.continuity_from_shot_id) total += 20;
  });
  total += Math.max(5, project.brief.duration_seconds * 0.15);
  return Math.round(total);
}

function showProductionEstimate(seconds) {
  state.jobEstimatedSeconds = Math.max(0, Number(seconds) || 0);
  $("#production-estimate").textContent = state.jobEstimatedSeconds
    ? `预计制作时间 ${formatTime(state.jobEstimatedSeconds)}`
    : "制作时间将根据镜头动态估算";
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadHealth() {
  state.health = await api("/api/health");
  const status = $("#engine-status");
  const agentReady = state.health.planner !== "heuristic";
  const prefix = agentReady ? "AI Agent · " : "";
  const healthyCount = [state.health.fl2va_healthy, state.health.ref2va_healthy].filter(Boolean).length;
  if (healthyCount === 2) {
    status.textContent = `${prefix}两个制作引擎已连接`;
    status.className = "status-pill ready";
  } else if (healthyCount === 1) {
    status.textContent = `${prefix}一个制作引擎已连接`;
    status.className = "status-pill warning";
  } else {
    status.textContent = agentReady ? "AI Agent · 尚未连接视频引擎" : "模板规划 · 尚未连接视频引擎";
    status.className = "status-pill warning";
  }
}

async function loadAssets() {
  const query = $("#asset-search").value.trim();
  const kind = $("#asset-kind-filter").value;
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (kind) params.set("kind", kind);
  state.assets = await api(`/api/assets?${params}`);
  renderAssets();
}

async function loadProjects() {
  state.projects = await api("/api/projects");
  const selector = $("#project-selector");
  selector.innerHTML = state.projects
    .map((project) => `<option value="${project.id}">${escapeHtml(project.brief.title)}</option>`)
    .join("");
  if (state.project) selector.value = state.project.id;
}

async function restoreProjectJob() {
  if (!state.project) return;
  const params = new URLSearchParams(window.location.search);
  const storedJobId = window.localStorage.getItem(`long-video-studio-job:${state.project.id}`);
  const resumeJobId = params.get("job") || storedJobId;
  try {
    const job = resumeJobId
      ? await api(`/api/jobs/${resumeJobId}`)
      : await api(`/api/projects/${state.project.id}/jobs/latest`);
    if (job && job.project_id === state.project.id && ["queued", "running"].includes(job.status)) {
      monitorJob(job.id, job);
    } else if (job && job.project_id === state.project.id && job.status === "complete") {
      showCompletedJob(job);
    } else if (resumeJobId) {
      window.localStorage.removeItem(`long-video-studio-job:${state.project.id}`);
    }
  } catch (error) {
    if (resumeJobId) reportError(error);
  }
}

function populateBriefForm(project) {
  const brief = project.brief;
  $("#brief-title").value = brief.title || "";
  $("#brief-prompt").value = brief.prompt || "";
  $("#brief-duration").value = brief.duration_seconds || 60;
  $("#brief-aspect").value = brief.aspect_ratio || "16:9";
  $("#brief-style-preset").value = brief.style_preset || "cinematic";
  $("#brief-style-instructions").value = brief.style_instructions || "";
  $("#brief-subtitle-mode").value = brief.subtitle_mode || "none";
  state.quality = brief.quality || "draft";
  $$(".quality-switch button").forEach((button) => {
    button.classList.toggle("active", button.dataset.quality === state.quality);
  });
}

async function selectProject(projectId) {
  if (!projectId) return;
  window.clearInterval(state.jobTimer);
  window.clearInterval(state.jobClockTimer);
  state.activeJobId = null;
  state.plan = null;
  clearOutputPreview();
  state.selectedAssets.clear();
  state.project = await api(`/api/projects/${projectId}`);
  populateBriefForm(state.project);
  state.project.brief.reference_asset_ids.forEach((id) => state.selectedAssets.add(id));
  $("#selected-count").textContent = state.selectedAssets.size;
  $("#project-selector").value = projectId;
  renderAssets();
  renderProject();
  showProductionEstimate(estimateProjectSeconds(state.project));
  const params = new URLSearchParams(window.location.search);
  params.set("project", projectId);
  params.delete("job");
  window.history.replaceState({}, "", `${window.location.pathname}${params.toString() ? `?${params}` : ""}`);
  await restoreProjectJob();
}

function assetPreview(asset) {
  if (asset.kind === "image") {
    return `<img src="/api/assets/${asset.id}/content" alt="${escapeHtml(asset.caption)}" loading="lazy" />`;
  }
  if (asset.kind === "video") {
    return `<video src="/api/assets/${asset.id}/content" muted preload="metadata"></video>`;
  }
  const icon = asset.kind === "audio" ? "♫" : "◫";
  return `<span class="asset-placeholder">${icon}</span>`;
}

function assetRoleValues(asset) {
  return asset.roles?.length ? asset.roles : ["reference"];
}

function formatAssetRoles(asset) {
  const ordered = [...assetRoleValues(asset)].sort((left, right) => {
    if (left === "reference") return 1;
    if (right === "reference") return -1;
    return 0;
  });
  return ordered.map((role) => ASSET_ROLE_LABELS[role] || role).join("、");
}

function renderAssets() {
  $("#asset-count").textContent = state.assets.length;
  const grid = $("#asset-grid");
  if (!state.assets.length) {
    grid.innerHTML = `<p class="muted">还没有素材。拖入文件或扫描素材目录。</p>`;
    return;
  }
  grid.innerHTML = state.assets
    .map((asset) => {
      const selected = state.selectedAssets.has(asset.id);
      const roles = formatAssetRoles(asset);
      return `
        <article class="asset-card ${selected ? "selected" : ""}" data-asset-id="${asset.id}">
          <div class="asset-preview">${assetPreview(asset)}</div>
          <div class="asset-check">${selected ? "✓" : "○"}</div>
          <button class="asset-edit" data-edit-asset="${asset.id}" type="button">编辑</button>
          <div class="asset-info">
            <strong>${escapeHtml(asset.caption || asset.original_name)}</strong>
            <span>${escapeHtml(roles)} · ${escapeHtml(asset.tags.join(", ") || asset.kind)}</span>
          </div>
        </article>`;
    })
    .join("");

  $$(".asset-card").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.closest("[data-edit-asset]")) return;
      const id = card.dataset.assetId;
      if (state.selectedAssets.has(id)) state.selectedAssets.delete(id);
      else state.selectedAssets.add(id);
      $("#selected-count").textContent = state.selectedAssets.size;
      renderAssets();
    });
  });
  $$('[data-edit-asset]').forEach((button) => {
    button.addEventListener("click", () => openAssetDialog(button.dataset.editAsset));
  });
}

async function uploadFiles(files) {
  if (!files.length) return;
  const form = new FormData();
  [...files].forEach((file) => form.append("files", file));
  form.append("tags", $("#asset-tags").value);
  form.append("roles", $("#asset-role").value);
  toast(`正在导入 ${files.length} 个素材…`);
  const imported = await api("/api/assets/upload", { method: "POST", body: form });
  imported.forEach((asset) => state.selectedAssets.add(asset.id));
  $("#selected-count").textContent = state.selectedAssets.size;
  await loadAssets();
  toast(`已导入 ${imported.length} 个素材`);
}

async function importPath() {
  const path = $("#import-path").value.trim();
  if (!path) return toast("请输入服务端素材目录");
  toast("正在扫描素材目录…");
  const imported = await api("/api/assets/import-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path,
      recursive: true,
      tags: $("#asset-tags").value.split(",").map((item) => item.trim()).filter(Boolean),
      roles: [$("#asset-role").value],
    }),
  });
  imported.forEach((asset) => state.selectedAssets.add(asset.id));
  $("#selected-count").textContent = state.selectedAssets.size;
  await loadAssets();
  toast(`从目录导入 ${imported.length} 个素材`);
}

function openAssetDialog(assetId) {
  const asset = state.assets.find((item) => item.id === assetId);
  if (!asset) return;
  $("#edit-asset-id").value = asset.id;
  $("#edit-asset-caption").value = asset.caption || "";
  $("#edit-asset-tags").value = asset.tags.join(", ");
  const roles = new Set(assetRoleValues(asset));
  [...$("#edit-asset-role").options].forEach((option) => {
    option.selected = roles.has(option.value);
  });
  $("#asset-dialog").showModal();
}

async function saveAsset(event) {
  event.preventDefault();
  const id = $("#edit-asset-id").value;
  const roles = [...$("#edit-asset-role").selectedOptions].map((option) => option.value);
  await api(`/api/assets/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      caption: $("#edit-asset-caption").value.trim(),
      tags: $("#edit-asset-tags").value.split(",").map((item) => item.trim()).filter(Boolean),
      roles: roles.length ? roles : ["reference"],
    }),
  });
  $("#asset-dialog").close();
  await loadAssets();
  toast("素材信息已更新");
}

async function planProject(event) {
  event.preventDefault();
  const prompt = $("#brief-prompt").value.trim();
  if (prompt.length < 3) return toast("请先描述你想制作的故事");
  const button = event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  button.firstElementChild.textContent = "正在构思故事…";
  try {
    const previousProjectId = state.project?.id;
    const project = await api("/api/projects/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: $("#brief-title").value.trim() || "Untitled film",
        prompt,
        duration_seconds: Number($("#brief-duration").value),
        aspect_ratio: $("#brief-aspect").value,
        style_preset: $("#brief-style-preset").value,
        style_instructions: $("#brief-style-instructions").value.trim(),
        subtitle_mode: $("#brief-subtitle-mode").value,
        reference_asset_ids: [...state.selectedAssets],
        quality: state.quality,
      }),
    });
    if (previousProjectId) {
      window.localStorage.removeItem(`long-video-studio-job:${previousProjectId}`);
    }
    clearOutputPreview();
    state.project = project;
    state.plan = null;
    state.activeJobId = null;
    await loadProjects();
    $("#project-selector").value = state.project.id;
    window.history.replaceState({}, "", `${window.location.pathname}?project=${state.project.id}`);
    renderProject();
    toast(`已生成 ${state.project.shots.length} 个镜头`);
  } finally {
    button.disabled = false;
    button.firstElementChild.textContent = "开始构思故事板";
  }
}

function referenceImageFor(shot) {
  if (shot.anchor_frame_path) {
    return `/api/projects/${state.project.id}/shots/${shot.id}/anchor?updated=${encodeURIComponent(shot.anchor_frame_path)}`;
  }
  const previous = state.project.shots.find((item) => item.index === shot.index - 1);
  if (previous?.boundary_frame_path) {
    return `/api/projects/${state.project.id}/shots/${previous.id}/boundary?updated=${encodeURIComponent(previous.boundary_frame_path)}`;
  }
  const id = shot.start_frame_asset_id || shot.reference_asset_ids?.find((assetId) => {
    const asset = state.assets.find((item) => item.id === assetId);
    return asset?.kind === "image";
  });
  return id ? `/api/assets/${id}/content` : "";
}

function renderProject() {
  if (!state.project) return;
  state.project.brief.reference_asset_ids.forEach((id) => state.selectedAssets.add(id));
  $("#selected-count").textContent = state.selectedAssets.size;
  $("#empty-state").classList.add("hidden");
  $("#project-view").classList.remove("hidden");
  $("#project-title").textContent = state.project.brief.title;
  $("#project-logline").textContent = state.project.world_bible.logline;
  $("#bible-style").textContent = state.project.world_bible.visual_style;
  $("#bible-characters").textContent = state.project.world_bible.character_notes.join("；");
  $("#bible-locations").textContent = state.project.world_bible.location_notes.join("；");
  $("#bible-continuity").textContent = state.project.world_bible.continuity_rules.slice(0, 2).join("；");
  const total = state.project.shots.reduce((sum, shot) => sum + shot.duration_seconds, 0);
  $("#storyboard-summary").textContent = `${state.project.shots.length} 个镜头 · ${formatTime(total)}`;
  renderStoryboard();
  renderTimeline();
}

function taskLabel(task) {
  return task === "ref2va" ? "参考驱动镜头" : "连续镜头";
}

function renderStoryboard() {
  const board = $("#storyboard");
  board.innerHTML = state.project.shots
    .map((shot) => {
      const image = referenceImageFor(shot);
      return `
        <article class="shot-card" data-shot-id="${shot.id}">
          <div class="shot-visual">
            ${image ? `<img src="${image}" alt="" />` : ""}
            <span class="shot-number">SHOT ${String(shot.index + 1).padStart(2, "0")}</span>
            <span class="shot-duration">${shot.duration_seconds}s</span>
          </div>
          <div class="shot-body">
            <h3>${escapeHtml(shot.title)}</h3>
            <p>${escapeHtml(shot.purpose)}</p>
            <div class="shot-meta">
              <span>${escapeHtml(taskLabel(shot.task))}</span>
              <span>${escapeHtml(shot.camera)}</span>
              <span>${shot.inference_steps} steps</span>
            </div>
            <div class="shot-edit-grid">
              <select class="shot-task">
                <option value="fl2va" ${shot.task === "fl2va" ? "selected" : ""}>连续镜头</option>
                <option value="ref2va" ${shot.task === "ref2va" ? "selected" : ""}>参考驱动</option>
              </select>
              <input class="shot-duration-input" type="number" min="4" max="15" step="0.5" value="${shot.duration_seconds}" />
            </div>
            <textarea class="shot-prompt">${escapeHtml(shot.prompt)}</textarea>
            <button class="save-shot" type="button">保存这个镜头</button>
          </div>
        </article>`;
    })
    .join("");
  $$(".save-shot").forEach((button) =>
    button.addEventListener("click", (event) => saveShot(event).catch(reportError)),
  );
}

function refreshStoryboardBoundaries() {
  if (!state.project) return;
  const cards = $$(".shot-card");
  state.project.shots.forEach((shot) => {
    const card = cards.find((item) => item.dataset.shotId === shot.id);
    const image = card?.querySelector(".shot-visual img");
    if (image) {
      const source = referenceImageFor(shot);
      if (source && image.src !== new URL(source, window.location.href).href) image.src = source;
    }
  });
}

async function saveShot(event) {
  const card = event.target.closest(".shot-card");
  const shotId = card.dataset.shotId;
  state.project = await api(`/api/projects/${state.project.id}/shots/${shotId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task: card.querySelector(".shot-task").value,
      duration_seconds: Number(card.querySelector(".shot-duration-input").value),
      prompt: card.querySelector(".shot-prompt").value.trim(),
    }),
  });
  state.plan = null;
  renderProject();
  toast("镜头已更新，后续制作计划会自动重编译");
}

function renderTimeline() {
  const timeline = $("#timeline");
  timeline.innerHTML = state.project.timeline
    .map((clip, index) => {
      const shot = state.project.shots.find((item) => item.id === clip.shot_id);
      const width = Math.max(100, Math.round(clip.duration_seconds * 11));
      return `<div class="timeline-clip" style="width:${width}px">
        <strong>${index + 1}. ${escapeHtml(shot?.title || "Shot")}</strong>
        <span>${clip.start_seconds.toFixed(1)}s — ${(clip.start_seconds + clip.duration_seconds).toFixed(1)}s</span>
      </div>`;
    })
    .join("");
}

async function compileProject() {
  if (!state.project) return;
  state.plan = await api(`/api/projects/${state.project.id}/compile`, { method: "POST" });
  $("#production-panel").classList.remove("hidden");
  showProductionEstimate(state.plan.estimated_seconds);
  const warnings = $("#production-warnings");
  warnings.innerHTML = state.plan.warnings.length
    ? state.plan.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")
    : "<li>所有制作能力已就绪。</li>";
  const videoReady = state.health.fl2va_healthy || state.health.ref2va_healthy;
  $("#render-project").disabled = !videoReady;
  toast(videoReady ? "制作计划已就绪" : "分镜已编译；连接视频引擎后即可制作");
}

async function renderFilm() {
  if (!state.project) return;
  if (!state.health.fl2va_healthy && !state.health.ref2va_healthy) {
    return toast("请先配置视频引擎 endpoint");
  }
  const button = $("#render-project");
  clearOutputPreview();
  button.disabled = true;
  button.textContent = "正在提交…";
  try {
    if (!state.plan) await compileProject();
    const job = await api(`/api/projects/${state.project.id}/render`, { method: "POST" });
    $("#job-progress").classList.remove("hidden");
    $("#production-panel").classList.add("job-active");
    $("#production-panel").scrollIntoView({ behavior: "smooth", block: "center" });
    button.textContent = "制作中…";
    monitorJob(job.id);
  } catch (error) {
    button.disabled = false;
    button.textContent = "开始制作";
    throw error;
  }
}

function updateJobProgress(job) {
  const observedProgress = Math.max(0, Math.min(1, Number(job.progress) || 0));
  if (job.created_at && !state.jobStartedAt) {
    state.jobStartedAt = Date.parse(job.created_at);
  }
  const elapsedSeconds = state.jobStartedAt
    ? Math.max(0, (Date.now() - state.jobStartedAt) / 1000)
    : 0;
  const estimatedProgress = state.jobEstimatedSeconds
    ? Math.min(0.99, elapsedSeconds / state.jobEstimatedSeconds)
    : observedProgress;
  const displayProgress = job.status === "complete"
    ? 1
    : Math.max(observedProgress, estimatedProgress);
  const percentage = Math.round(displayProgress * 100);
  $("#progress-fill").style.width = `${percentage}%`;
  $("#progress-percent").textContent = `${percentage}%`;
  const shotMessage = String(job.message || "正在准备制作").replace(
    /^rendering shot (\d+)\/(\d+)$/,
    "正在生成镜头 $1/$2",
  );
  $("#progress-message").textContent = shotMessage;
  if (job.status === "running" || job.status === "queued") {
    const remaining = state.jobEstimatedSeconds - elapsedSeconds;
    $("#progress-timing").textContent = remaining > 0
      ? `已用 ${formatTime(elapsedSeconds)} · 预计剩余 ${formatTime(remaining)}`
      : `已用 ${formatTime(elapsedSeconds)} · 即将完成，请稍候`;
  } else {
    $("#progress-timing").textContent = `总用时 ${formatTime(elapsedSeconds)}`;
  }
}

function showCompletedJob(job) {
  if (!state.project || job.project_id !== state.project.id || job.status !== "complete") return;
  $("#production-panel").classList.remove("hidden", "job-active");
  $("#job-progress").classList.remove("hidden");
  updateJobProgress(job);
  const video = $("#output-video");
  video.src = `/api/jobs/${job.id}/output`;
  video.load();
  video.classList.remove("hidden");
  const link = $("#output-link");
  link.href = `/api/jobs/${job.id}/output?download=true`;
  link.download = `${job.project_id}.mp4`;
  link.classList.remove("hidden");
  const subtitleLink = $("#subtitle-link");
  if (job.subtitle_path) {
    subtitleLink.href = `/api/jobs/${job.id}/subtitles`;
    subtitleLink.classList.remove("hidden");
    const track = document.createElement("track");
    track.kind = "subtitles";
    track.src = `/api/jobs/${job.id}/subtitles`;
    track.srclang = "zh";
    track.label = "外挂字幕";
    video.append(track);
  }
  $("#render-project").disabled = false;
  $("#render-project").textContent = "开始制作";
}

function clearOutputPreview() {
  const video = $("#output-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
  video.classList.add("hidden");
  const link = $("#output-link");
  link.removeAttribute("href");
  link.classList.add("hidden");
  const subtitleLink = $("#subtitle-link");
  subtitleLink.removeAttribute("href");
  subtitleLink.classList.add("hidden");
  video.querySelectorAll("track").forEach((track) => track.remove());
}

function monitorJob(jobId, initialJob = null) {
  window.clearInterval(state.jobTimer);
  window.clearInterval(state.jobClockTimer);
  state.activeJobId = jobId;
  state.jobStartedAt = initialJob?.created_at ? Date.parse(initialJob.created_at) : null;
  if (state.project) {
    window.localStorage.setItem(`long-video-studio-job:${state.project.id}`, jobId);
  }
  const params = new URLSearchParams(window.location.search);
  params.set("job", jobId);
  window.history.replaceState({}, "", `${window.location.pathname}?${params}`);
  const poll = async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      if (state.project && job.project_id === state.project.id) {
        const refreshed = await api(`/api/projects/${state.project.id}`);
        state.project = refreshed;
        renderStoryboard();
        renderTimeline();
        refreshStoryboardBoundaries();
      }
      updateJobProgress(job);
      if (job.status === "complete" || job.status === "failed") {
        window.clearInterval(state.jobTimer);
        window.clearInterval(state.jobClockTimer);
        $("#production-panel").classList.remove("job-active");
        $("#render-project").disabled = false;
        $("#render-project").textContent = "开始制作";
        state.activeJobId = null;
        if (state.project) {
          window.localStorage.removeItem(`long-video-studio-job:${state.project.id}`);
        }
        if (job.status === "complete") {
          showCompletedJob(job);
        }
        toast(job.status === "complete" ? "成片制作完成" : `制作失败：${job.error}`, 6000);
      }
    } catch (error) {
      $("#progress-message").textContent = `状态读取失败：${error.message}`;
    }
  };
  $("#job-progress").classList.remove("hidden");
  $("#production-panel").classList.add("job-active");
  poll();
  state.jobTimer = window.setInterval(poll, 3000);
  state.jobClockTimer = window.setInterval(() => {
    if (state.activeJobId) {
      const elapsed = state.jobStartedAt ? Math.max(0, (Date.now() - state.jobStartedAt) / 1000) : 0;
      const remaining = state.jobEstimatedSeconds - elapsed;
      $("#progress-timing").textContent = remaining > 0
        ? `已用 ${formatTime(elapsed)} · 预计剩余 ${formatTime(remaining)}`
        : `已用 ${formatTime(elapsed)} · 即将完成，请稍候`;
    }
  }, 1000);
}

function resetProject() {
  window.clearInterval(state.jobTimer);
  window.clearInterval(state.jobClockTimer);
  state.activeJobId = null;
  clearOutputPreview();
  state.project = null;
  state.plan = null;
  $("#project-view").classList.add("hidden");
  $("#production-panel").classList.add("hidden");
  $("#empty-state").classList.remove("hidden");
  $("#project-selector").value = "";
  $("#brief-prompt").focus();
}

function bindEvents() {
  $("#brief-form").addEventListener("submit", (event) => planProject(event).catch(reportError));
  $("#project-selector").addEventListener("change", (event) => selectProject(event.target.value).catch(reportError));
  $("#new-project").addEventListener("click", resetProject);
  $("#compile-project").addEventListener("click", () => compileProject().catch(reportError));
  $("#render-project").addEventListener("click", () => renderFilm().catch(reportError));
  $("#asset-search").addEventListener("input", () => loadAssets().catch(reportError));
  $("#asset-kind-filter").addEventListener("change", () => loadAssets().catch(reportError));
  $("#import-path-button").addEventListener("click", () => importPath().catch(reportError));
  $("#save-asset").addEventListener("click", (event) => saveAsset(event).catch(reportError));
  $$(".quality-switch button").forEach((button) => {
    button.addEventListener("click", () => {
      state.quality = button.dataset.quality;
      $$(".quality-switch button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
    });
  });

  const fileInput = $("#asset-files");
  const dropZone = $("#drop-zone");
  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") fileInput.click();
  });
  fileInput.addEventListener("change", () => uploadFiles(fileInput.files).catch(reportError));
  ["dragenter", "dragover"].forEach((name) =>
    dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragging");
    }),
  );
  ["dragleave", "drop"].forEach((name) =>
    dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.remove("dragging");
    }),
  );
  dropZone.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files).catch(reportError));
}

function reportError(error) {
  console.error(error);
  toast(error.message || "操作失败", 6000);
}

async function boot() {
  bindEvents();
  try {
    await Promise.all([loadHealth(), loadAssets()]);
    await loadProjects();
    if (state.projects.length) {
      const params = new URLSearchParams(window.location.search);
      await selectProject(params.get("project") || state.projects[0].id);
    }
  } catch (error) {
    reportError(error);
  }
}

boot();
