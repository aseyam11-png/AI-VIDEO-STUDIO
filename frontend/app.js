(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    projects: [],
    project: null,
    assets: [],
    selectedAsset: null,
    workflow: null,
    pollHandle: null,
    uploading: false
  };

  const statusLabels = {
    awaiting_approval: "بانتظار موافقتك",
    queued: "في قائمة التنفيذ",
    running: "قيد التنفيذ",
    cancel_requested: "جارٍ الإيقاف",
    succeeded: "اكتمل بنجاح",
    failed: "تعذّر التنفيذ",
    cancelled: "تم الإلغاء",
    planned: "مخطط",
    failed_step: "فشل"
  };

  function notice(message, type = "info") {
    const box = $("notice");
    box.textContent = message;
    box.className = `notice ${type}`;
    box.hidden = !message;
  }

  function clearNotice() {
    $("notice").hidden = true;
    $("notice").textContent = "";
  }

  function setApiStatus(label, ok) {
    $("api-status").textContent = label;
    const led = $("api-status").previousElementSibling;
    if (led) {
      led.style.background = ok ? "var(--success)" : "var(--danger)";
      led.style.boxShadow = ok ? "0 0 0 4px #76b89b18" : "0 0 0 4px #d9828218";
    }
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(path, {
      ...options,
      headers,
      credentials: "same-origin"
    });
    const text = await response.text();
    let data = {};
    if (text) {
      try { data = JSON.parse(text); }
      catch { data = { detail: text.slice(0, 500) }; }
    }
    if (!response.ok) {
      const detail = typeof data.detail === "string"
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail.map((item) => item.msg || JSON.stringify(item)).join("، ")
          : `HTTP ${response.status}`;
      throw new Error(detail);
    }
    return data;
  }

  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    return [...bytes].map((byte, index) => {
      const hex = byte.toString(16).padStart(2, "0");
      return [4, 6, 8, 10].includes(index) ? "-" + hex : hex;
    }).join("");
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return "";
    if (bytes < 1024) return `${bytes} بايت`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds)) return "";
    if (seconds < 60) return `${Math.round(seconds)} ثانية`;
    return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")} دقيقة`;
  }

  function setStepper(current) {
    document.querySelectorAll(".stepper .step").forEach((item) => {
      const number = Number(item.dataset.step);
      item.classList.toggle("active", number === current);
      item.classList.toggle("done", number < current);
    });
  }

  function setProjectControls() {
    const ready = Boolean(state.project && state.selectedAsset && !state.uploading);
    $("generate-plan").disabled = !ready;
    $("project-summary").textContent = state.project
      ? `${state.project.name} · ${state.project.target_resolution || "الدقة الافتراضية"} · ${state.project.language === "en" ? "English" : "العربية"}`
      : "اختر مشروعًا أو أنشئ مشروعًا جديدًا.";
  }

  async function healthCheck() {
    try {
      const [health, ready] = await Promise.all([
        api("/health"),
        api("/ready")
      ]);
      setApiStatus(ready.status === "ready" ? "الـAPI وقاعدة البيانات جاهزان" : "الـAPI متصل", true);
    } catch (error) {
      setApiStatus("تعذّر الاتصال أو قاعدة البيانات غير جاهزة", false);
    }
  }

  async function loadProjects(preferredId) {
    const projects = await api("/v1/projects?limit=100");
    state.projects = Array.isArray(projects) ? projects : [];
    const select = $("project-select");
    select.replaceChildren();
    if (!state.projects.length) {
      const option = new Option("لا توجد مشاريع بعد", "");
      select.add(option);
      state.project = null;
      state.assets = [];
      state.selectedAsset = null;
      renderAssets();
      setProjectControls();
      return;
    }
    for (const project of state.projects) {
      select.add(new Option(project.name || project.id, project.id));
    }
    const savedId = preferredId || localStorage.getItem("studio.projectId");
    const match = state.projects.find((project) => project.id === savedId);
    select.value = match ? match.id : state.projects[0].id;
    await loadProject(select.value);
  }

  async function loadProject(projectId) {
    if (!projectId) return;
    try {
      const project = await api(`/v1/projects/${encodeURIComponent(projectId)}`);
      state.project = project;
      localStorage.setItem("studio.projectId", projectId);
      const assets = await api(`/v1/projects/${encodeURIComponent(projectId)}/assets`);
      state.assets = Array.isArray(assets) ? assets : [];
      const preferredAsset = localStorage.getItem("studio.assetId");
      const matching = state.assets.find((asset) => asset.id === preferredAsset && asset.asset_type === "video");
      state.selectedAsset = matching || state.assets.find((asset) => asset.asset_type === "video") || null;
      renderAssets();
      setProjectControls();
      const savedWorkflow = localStorage.getItem("studio.workflowId");
      if (savedWorkflow && localStorage.getItem("studio.workflowProjectId") === projectId) {
        try {
          await refreshWorkflow(savedWorkflow, false);
          if (state.workflow && ["queued", "running", "cancel_requested"].includes(state.workflow.status)) {
            startPolling(savedWorkflow);
          }
        } catch {
          localStorage.removeItem("studio.workflowId");
        }
      }
    } catch (error) {
      notice(`تعذّر تحميل المشروع: ${error.message}`, "error");
    }
  }

  function renderAssets() {
    const select = $("asset-select");
    select.replaceChildren();
    const videos = state.assets.filter((asset) => asset.asset_type === "video");
    if (!videos.length) {
      select.add(new Option("لا توجد فيديوهات في هذا المشروع", ""));
      select.disabled = true;
      state.selectedAsset = null;
      $("asset-name").textContent = "لم يتم رفع فيديو بعد";
      $("asset-meta").textContent = "ارفع فيديو لبدء إنشاء خطة الإنتاج";
    } else {
      for (const asset of videos) {
        const label = `${asset.original_filename || asset.stored_filename} · ${formatBytes(asset.size_bytes)}`;
        select.add(new Option(label, asset.id));
      }
      select.disabled = false;
      select.value = state.selectedAsset?.id || videos[0].id;
      state.selectedAsset = videos.find((asset) => asset.id === select.value) || videos[0];
      $("asset-name").textContent = state.selectedAsset.original_filename || state.selectedAsset.stored_filename;
      $("asset-meta").textContent = `${formatBytes(state.selectedAsset.size_bytes)} · ${state.selectedAsset.status || "ready"}`;
    }
    setProjectControls();
  }

  function uploadFile(file) {
    if (!state.project) {
      notice("أنشئ مشروعًا أو اختر مشروعًا قبل رفع الفيديو.", "error");
      return;
    }
    const allowed = [".mp4", ".mov", ".mkv", ".webm", ".m4v"];
    const name = file.name.toLowerCase();
    if (!allowed.some((extension) => name.endsWith(extension))) {
      notice("صيغة الملف غير مدعومة. استخدم MP4 أو MOV أو MKV أو WEBM أو M4V.", "error");
      return;
    }

    const progressWrap = $("upload-progress-wrap");
    const progress = $("upload-progress");
    const percent = $("upload-percent");
    $("upload-filename").textContent = file.name;
    progressWrap.hidden = false;
    progress.style.width = "0%";
    percent.textContent = "0%";
    state.uploading = true;
    setProjectControls();
    notice("بدأ رفع الفيديو إلى مساحة التخزين الخاصة…", "info");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/v1/projects/${encodeURIComponent(state.project.id)}/assets`);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const value = Math.round((event.loaded / event.total) * 100);
        progress.style.width = `${value}%`;
        percent.textContent = `${value}%`;
      }
    };
    xhr.onload = async () => {
      state.uploading = false;
      setProjectControls();
      let body = {};
      try { body = xhr.responseText ? JSON.parse(xhr.responseText) : {}; }
      catch { body = { detail: xhr.responseText }; }
      if (xhr.status < 200 || xhr.status >= 300) {
        progressWrap.hidden = true;
        notice(body.detail || `فشل رفع الملف (HTTP ${xhr.status}).`, "error");
        return;
      }
      progress.style.width = "100%";
      percent.textContent = "100%";
      notice("تم رفع الفيديو والتحقق من صلاحيته بنجاح.", "success");
      try {
        await reloadAssets(body.id);
      } catch (error) {
        notice(`تم رفع الملف، لكن تعذّر تحديث قائمة المواد: ${error.message}`, "error");
      }
    };
    xhr.onerror = () => {
      state.uploading = false;
      progressWrap.hidden = true;
      setProjectControls();
      notice("انقطع الاتصال أثناء رفع الملف. تحقق من الشبكة ثم أعد المحاولة.", "error");
    };
    xhr.send((() => {
      const form = new FormData();
      form.append("file", file, file.name);
      return form;
    })());
  }

  async function reloadAssets(preferredId) {
    const assets = await api(`/v1/projects/${encodeURIComponent(state.project.id)}/assets`);
    state.assets = Array.isArray(assets) ? assets : [];
    const preferred = state.assets.find((asset) => asset.id === preferredId && asset.asset_type === "video");
    state.selectedAsset = preferred || state.assets.find((asset) => asset.asset_type === "video") || null;
    if (state.selectedAsset) localStorage.setItem("studio.assetId", state.selectedAsset.id);
    renderAssets();
  }

  function renderPlan(plan) {
    const panel = $("plan-panel");
    const content = $("plan-content");
    content.replaceChildren();

    const summary = document.createElement("div");
    summary.className = "plan-summary";
    const title = document.createElement("h4");
    title.textContent = plan.title || "خطة الإنتاج";
    const logline = document.createElement("p");
    logline.textContent = plan.logline || "";
    const facts = document.createElement("div");
    facts.className = "plan-facts";
    [
      `المدة: ${formatDuration(plan.target_duration_seconds)}`,
      `الجمهور: ${plan.audience || "غير محدد"}`,
      `الأسلوب: ${plan.visual_style || "غير محدد"}`
    ].forEach((value) => {
      const fact = document.createElement("span");
      fact.className = "fact";
      fact.textContent = value;
      facts.append(fact);
    });
    summary.append(title, logline, facts);
    content.append(summary);

    const scenes = document.createElement("div");
    scenes.className = "scene-list";
    (plan.scenes || []).forEach((scene) => {
      const card = document.createElement("article");
      card.className = "scene-card";
      const index = document.createElement("span");
      index.className = "scene-index";
      index.textContent = String(scene.index).padStart(2, "0");
      const body = document.createElement("div");
      const heading = document.createElement("h5");
      heading.textContent = scene.purpose || `المشهد ${scene.index}`;
      const description = document.createElement("p");
      description.textContent = scene.visual_description || "";
      const meta = document.createElement("div");
      meta.className = "scene-meta";
      [formatDuration(scene.duration_seconds), scene.shot_type, scene.camera_movement].filter(Boolean).forEach((value) => {
        const item = document.createElement("span");
        item.textContent = value;
        meta.append(item);
      });
      body.append(heading, description, meta);
      card.append(index, body);
      scenes.append(card);
    });
    content.append(scenes);
    panel.hidden = false;
    $("plan-status").textContent = "بانتظار المراجعة";
    $("approval-actions").hidden = state.workflow?.status !== "awaiting_approval";
    setStepper(2);
  }

  function renderWorkflowSteps(steps) {
    const list = $("workflow-steps");
    list.replaceChildren();
    (steps || []).forEach((step) => {
      const item = document.createElement("article");
      item.className = "workflow-step";
      item.dataset.state = step.status;
      const number = document.createElement("span");
      number.className = "step-index";
      number.textContent = String(step.sequence).padStart(2, "0");
      const body = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = toolLabel(step.tool_id);
      const detail = document.createElement("small");
      detail.textContent = step.error_message || (step.result?.message || `المهمة: ${step.tool_id}`);
      body.append(title, detail);
      const stateLabel = document.createElement("span");
      stateLabel.className = "step-state";
      stateLabel.textContent = statusLabels[step.status] || step.status;
      item.append(number, body, stateLabel);
      if (["failed", "cancelled"].includes(step.status) && step.job_id) {
        const retry = document.createElement("button");
        retry.className = "retry-button";
        retry.type = "button";
        retry.textContent = "إعادة المحاولة";
        retry.addEventListener("click", () => retryJob(step.job_id));
        body.append(retry);
      }
      list.append(item);
    });
  }

  function toolLabel(tool) {
    const labels = {
      "vision.detect": "اكتشاف العناصر — Grounding DINO",
      "vision.track": "تتبع العناصر — SAM 2",
      "audio.enhance": "تحسين الصوت — DeepFilterNet",
      "speech.transcribe": "تفريغ الكلام — WhisperX",
      "media.proxy": "إنشاء نسخة تحرير خفيفة",
      "media.scenes": "تحليل المشاهد",
      "media.timeline": "بناء خط التحرير الزمني",
      "media.render": "تصدير فيديو MP4",
      "video.generate": "توليد لقطة فيديو — Wan 2.2",
      "video.interpolate": "تحسين سلاسة الحركة — RIFE",
      "video.upscale": "رفع جودة الإطارات — Real-ESRGAN",
      "motion.blender": "تصيير الموشن جرافيك — Blender"
    };
    return labels[tool] || tool || "مهمة إنتاج";
  }

  function renderWorkflow(workflow) {
    state.workflow = workflow;
    $("execution-panel").hidden = false;
    $("workflow-state").textContent = statusLabels[workflow.status] || workflow.status;
    $("plan-status").textContent = statusLabels[workflow.status] || workflow.status;
    $("approval-actions").hidden = workflow.status !== "awaiting_approval";
    $("execution-actions").hidden = !["queued", "running", "cancel_requested"].includes(workflow.status);
    renderWorkflowSteps(workflow.steps || []);
    $("plan-panel").hidden = !workflow.creative_plan;
    if (workflow.creative_plan) renderPlan(workflow.creative_plan);

    const terminal = ["succeeded", "failed", "cancelled"].includes(workflow.status);
    if (workflow.status === "succeeded") {
      setStepper(4);
      renderFinalVideo(workflow);
    } else {
      $("render-result").hidden = true;
      $("video-preview-wrap").hidden = true;
      if (workflow.status === "running" || workflow.status === "queued") setStepper(3);
    }
    if (workflow.status === "failed") {
      notice(workflow.error_message || "فشل أحد أجزاء التنفيذ. راجع تفاصيل الخطوات ويمكن إعادة محاولة المهمة الفاشلة.", "error");
    } else if (workflow.status === "cancelled") {
      notice("تم إلغاء سير العمل.", "info");
    } else if (workflow.status === "succeeded") {
      notice("اكتمل سير العمل وتم إنشاء ملف الفيديو النهائي.", "success");
    }
    return terminal;
  }

  function renderFinalVideo(workflow) {
    const finalStep = [...(workflow.steps || [])].reverse().find((step) => step.tool_id === "media.render");
    const downloadUrl = finalStep?.result?.download_url;
    if (!downloadUrl) {
      $("render-result").hidden = true;
      return;
    }
    const url = new URL(downloadUrl, window.location.origin);
    if (url.origin !== window.location.origin) {
      notice("رفض الاستديو رابط تنزيل خارج نطاقه المحلي.", "error");
      return;
    }
    $("download-video").href = url.pathname + url.search;
    $("render-meta").textContent = `تم تصدير ملف MP4 · ${formatDuration(finalStep.result.duration_seconds)}`;
    $("render-result").hidden = false;
    $("video-preview").src = url.pathname + url.search;
    $("video-preview-wrap").hidden = false;
  }

  async function createPlan(event) {
    event.preventDefault();
    clearNotice();
    if (!state.project || !state.selectedAsset) {
      notice("اختر مشروعًا وارفع فيديو أولًا.", "error");
      return;
    }
    const brief = $("creative-brief").value.trim();
    if (brief.length < 20) {
      notice("اكتب وصفًا من 20 حرفًا على الأقل حتى يستطيع المخرج فهم المطلوب.", "error");
      return;
    }
    const button = $("generate-plan");
    button.disabled = true;
    button.textContent = "جارٍ إعداد الخطة بواسطة Qwen…";
    try {
      const payload = {
        project_id: state.project.id,
        asset_id: state.selectedAsset.id,
        idempotency_key: `studio-${uuid()}`,
        brief,
        language: $("plan-language").value,
        target_duration_seconds: Number($("target-duration").value),
        transcribe_audio: $("transcribe-audio").checked,
        enhance_audio: $("enhance-audio").checked,
        detection_prompt: $("detection-prompt").value.trim() || null,
        tracking_prompt: $("tracking-prompt").value.trim() || null,
        generate_video: false,
        interpolate_generated_video: false
      };
      const workflow = await api("/v1/workflows/from-brief", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      state.workflow = workflow;
      localStorage.setItem("studio.workflowId", workflow.id);
      localStorage.setItem("studio.workflowProjectId", state.project.id);
      renderWorkflow(workflow);
      notice("تم إنشاء الخطة. راجع المشاهد ثم اعتمدها لبدء التنفيذ.", "success");
    } catch (error) {
      notice(`تعذّر إنشاء خطة الإنتاج: ${error.message}`, "error");
    } finally {
      button.innerHTML = "<span>✦</span> إنشاء خطة الإنتاج بالذكاء الاصطناعي";
      setProjectControls();
    }
  }

  async function approveWorkflow() {
    if (!state.workflow?.id) return;
    $("approve-workflow").disabled = true;
    try {
      const workflow = await api(`/v1/workflows/${encodeURIComponent(state.workflow.id)}/approve`, { method: "POST" });
      renderWorkflow(workflow);
      notice("تم اعتماد الخطة وإرسال أول مهمة إلى محرك التنفيذ.", "success");
      startPolling(workflow.id);
    } catch (error) {
      notice(`تعذّر اعتماد الخطة: ${error.message}`, "error");
    } finally {
      $("approve-workflow").disabled = false;
    }
  }

  async function refreshWorkflow(workflowId, showErrors = true) {
    try {
      const workflow = await api(`/v1/workflows/${encodeURIComponent(workflowId)}`);
      const terminal = renderWorkflow(workflow);
      if (terminal) stopPolling();
      return workflow;
    } catch (error) {
      if (showErrors) notice(`تعذّر تحديث حالة التنفيذ: ${error.message}`, "error");
      throw error;
    }
  }

  function startPolling(workflowId) {
    stopPolling();
    refreshWorkflow(workflowId).catch(() => {});
    state.pollHandle = window.setInterval(() => {
      refreshWorkflow(workflowId, false).catch(() => {});
    }, 2500);
  }

  function stopPolling() {
    if (state.pollHandle) window.clearInterval(state.pollHandle);
    state.pollHandle = null;
  }

  async function cancelWorkflow() {
    if (!state.workflow?.id) return;
    $("stop-workflow").disabled = true;
    try {
      const workflow = await api(`/v1/workflows/${encodeURIComponent(state.workflow.id)}/cancel`, { method: "POST" });
      renderWorkflow(workflow);
      if (["queued", "running", "cancel_requested"].includes(workflow.status)) startPolling(workflow.id);
      else stopPolling();
    } catch (error) {
      notice(`تعذّر إلغاء التنفيذ: ${error.message}`, "error");
    } finally {
      $("stop-workflow").disabled = false;
    }
  }

  async function cancelDraft() {
    if (!state.workflow?.id) return;
    try {
      const workflow = await api(`/v1/workflows/${encodeURIComponent(state.workflow.id)}/cancel`, { method: "POST" });
      renderWorkflow(workflow);
      stopPolling();
      notice("تم إلغاء خطة الإنتاج دون تشغيل مهام.", "info");
    } catch (error) {
      notice(`تعذّر إلغاء الخطة: ${error.message}`, "error");
    }
  }

  async function retryJob(jobId) {
    try {
      await api(`/v1/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
      notice("تمت جدولة إعادة المحاولة. ستظهر حالة المهمة في القائمة.", "success");
      if (state.workflow?.id) startPolling(state.workflow.id);
    } catch (error) {
      notice(`تعذّرت إعادة المحاولة: ${error.message}`, "error");
    }
  }

  async function createProject(event) {
    event.preventDefault();
    const form = new FormData($("project-form"));
    const payload = {
      name: String(form.get("name") || "").trim(),
      brief: String(form.get("brief") || "").trim(),
      target_duration_seconds: Number(form.get("target_duration_seconds")),
      target_resolution: String(form.get("target_resolution")),
      target_fps: 30,
      language: "ar"
    };
    const submit = $("project-form").querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      const project = await api("/v1/projects", { method: "POST", body: JSON.stringify(payload) });
      await loadProjects(project.id);
      notice("تم إنشاء المشروع بنجاح. ارفع الفيديو للمتابعة.", "success");
      $("creative-brief").value = payload.brief;
      localStorage.removeItem("studio.workflowId");
      $("plan-panel").hidden = true;
      $("execution-panel").hidden = true;
    } catch (error) {
      notice(`تعذّر إنشاء المشروع: ${error.message}`, "error");
    } finally {
      submit.disabled = false;
    }
  }

  function bindUpload() {
    const input = $("media-file");
    $("choose-file").addEventListener("click", () => input.click());
    input.addEventListener("change", () => {
      if (input.files?.[0]) uploadFile(input.files[0]);
      input.value = "";
    });
    const zone = $("upload-zone");
    ["dragenter", "dragover"].forEach((eventName) => zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.add("dragover");
    }));
    ["dragleave", "drop"].forEach((eventName) => zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.remove("dragover");
    }));
    zone.addEventListener("drop", (event) => {
      const file = event.dataTransfer?.files?.[0];
      if (file) uploadFile(file);
    });
  }

  async function boot() {
    $("project-form").addEventListener("submit", createProject);
    $("director-form").addEventListener("submit", createPlan);
    $("project-select").addEventListener("change", (event) => {
      stopPolling();
      state.workflow = null;
      $("plan-panel").hidden = true;
      $("execution-panel").hidden = true;
      loadProject(event.target.value);
    });
    $("asset-select").addEventListener("change", (event) => {
      state.selectedAsset = state.assets.find((asset) => asset.id === event.target.value) || null;
      if (state.selectedAsset) localStorage.setItem("studio.assetId", state.selectedAsset.id);
      renderAssets();
    });
    $("approve-workflow").addEventListener("click", approveWorkflow);
    $("cancel-workflow").addEventListener("click", cancelDraft);
    $("stop-workflow").addEventListener("click", cancelWorkflow);
    bindUpload();
    await healthCheck();
    try {
      await loadProjects();
      if (!state.projects.length) notice("ابدأ بإنشاء مشروع جديد من القائمة الجانبية.", "info");
    } catch (error) {
      notice(`تعذّر تحميل المشاريع: ${error.message}. تأكد من تشغيل الخدمات وتطبيق migrations.`, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    boot().catch((error) => notice(error.message, "error"));
  });
})();
