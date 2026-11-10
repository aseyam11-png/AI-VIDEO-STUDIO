(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    projects: [],
    modelStatus: null,
    project: null,
    assets: [],
    selectedAsset: null,
    timelineManifest: null,
    timelineClips: [],
    timelineDirty: false,
    timelineRendered: false,
    audioMixManifest: null,
    audioMixClips: [],
    audioMixDirty: false,
    audioMixLevels: { voice: 0, music: 0, sfx: 0 },
    sourceAudioEnabled: true,
    sourceAudioDenoise: false,
    visualEffectsManifest: null,
    visualTransitions: [],
    visualTitles: [],
    visualOverlays: [],
    visualEffectsDirty: false,
    sceneManifest: null,
    sceneAssetId: null,
    transcriptionManifest: null,
    transcriptionAssetId: null,
    subtitleManifest: null,
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
          : data.detail && typeof data.detail === "object"
            ? data.detail.message || JSON.stringify(data.detail)
            : `HTTP ${response.status}`;
      const error = new Error(detail);
      error.status = response.status;
      throw error;
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
    const qwenReady = Boolean(state.modelStatus?.models?.["qwen3.5-9b-q4_K_M"]?.ready);
    const ready = Boolean(state.project && state.selectedAsset && !state.uploading && qwenReady);
    $("generate-plan").disabled = !ready;
    const enhancement = $("upscale-video");
    if (enhancement) {
      const configured = Boolean(state.modelStatus?.models?.realesrgan?.workflow_configured);
      const duration = Number(state.selectedAsset?.probe?.format?.duration);
      const supportedDuration = Number.isFinite(duration) && duration > 0 && duration <= 10;
      enhancement.disabled = !configured || !supportedDuration;
      const label = enhancement.closest("label");
      if (label) label.classList.toggle("option-disabled", enhancement.disabled);
    }
    $("project-summary").textContent = state.project
      ? `${state.project.name} · ${state.project.target_resolution || "الدقة الافتراضية"} · ${state.project.language === "en" ? "English" : "العربية"}`
      : "اختر مشروعًا أو أنشئ مشروعًا جديدًا.";
  }

  async function loadModelStatus() {
    const director = $("director-readiness");
    const summary = $("model-readiness");
    const refreshButton = $("refresh-model-status");
    if (refreshButton) refreshButton.disabled = true;
    try {
      const status = await api("/v1/models/status", { signal: AbortSignal.timeout(310000) });
      state.modelStatus = status;
      const models = status.models || {};
      const qwen = models["qwen3.5-9b-q4_K_M"] || {};
      if (qwen.ready) {
        director.textContent = "Qwen موجود في Ollama. لم يُعتمد الاستدلال الحقيقي بعد؛ اختبر الخطة الأولى قبل الإنتاج.";
        director.className = "hint readiness-good";
      } else {
        director.textContent = "المخرج الذكي غير جاهز: شغّل Ollama وثبّت النموذج qwen3.5:9b-q4_K_M ثم أعد تحميل الصفحة.";
        director.className = "hint readiness-warning";
      }

      const whisper = models.whisperx || {};
      const deepfilter = models.deepfilternet || {};
      const grounding = models["grounding-dino"] || {};
      const sam2 = models.sam2 || {};
      const realesrgan = models.realesrgan || {};
      const optional = [
        { id: "transcribe-audio", ready: Boolean(whisper.worker_enabled), label: "WhisperX" },
        { id: "enhance-audio", ready: Boolean(deepfilter.configured), label: "DeepFilterNet" },
        { id: "upscale-video", ready: Boolean(realesrgan.workflow_configured), label: "Real-ESRGAN" },
        { id: "detection-prompt", ready: Boolean(grounding.workflow_configured), label: "Grounding DINO" },
        { id: "tracking-prompt", ready: Boolean(sam2.workflow_configured), label: "SAM 2" }
      ];
      optional.forEach((item) => {
        const input = $(item.id);
        input.disabled = !item.ready;
        const label = input.closest("label") || input.parentElement;
        if (label) label.classList.toggle("option-disabled", !item.ready);
      });
      const readyNames = optional.filter((item) => item.ready).map((item) => item.label);
      const missingNames = optional.filter((item) => !item.ready).map((item) => item.label);
      const messages = [];
      messages.push(qwen.ready ? "Qwen: النموذج موجود" : "Qwen: غير جاهز");
      if (readyNames.length) messages.push("خيارات مهيأة: " + readyNames.join("، "));
      if (missingNames.length) messages.push("غير مهيأ: " + missingNames.join("، "));
      messages.push("جاهزية الإعداد لا تعني إثبات جودة الاستدلال.");
      summary.textContent = messages.join(" · ");
      summary.className = "hint readiness-summary";
      setProjectControls();
    } catch (error) {
      state.modelStatus = null;
      director.textContent = "تعذّر فحص Ollama. تأكد من تشغيل الـAPI والاتصال بخدمة النماذج.";
      director.className = "hint readiness-warning";
      summary.textContent = "حالة الخدمات غير معروفة. لم يتم تمكين الخيارات الاختيارية.";
      summary.className = "hint readiness-warning";
      ["transcribe-audio","enhance-audio","upscale-video","detection-prompt","tracking-prompt"].forEach((id) => {
        $(id).disabled = true;
      });
      setProjectControls();
    } finally {
      if (refreshButton) refreshButton.disabled = false;
    }
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
      await loadProjectTimeline(projectId);
      await loadProjectAudioMix(projectId);
      await loadProjectVisualEffects(projectId);
      await loadAssetScenes($("timeline-source").value);
      await loadAssetTranscription($("timeline-source").value);
      await loadTimelineSubtitleStatus(projectId);
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
    renderTimelineEditor();
    renderAudioMixEditor();
    renderVisualEffectsEditor();
    setProjectControls();
  }

  function getAssetDuration(asset) {
    const value = Number(asset?.probe?.format?.duration);
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  function getAssetLabel(asset) {
    return asset?.original_filename || asset?.stored_filename || "فيديو دون اسم";
  }

  function timelineEndpoint() {
    return state.project
      ? `/v1/projects/${encodeURIComponent(state.project.id)}/timeline`
      : null;
  }

  function timelineTotalDuration() {
    return state.timelineClips.reduce((total, clip) => total + Number(clip.duration_seconds || 0), 0);
  }

  function resetTimeline() {
    state.timelineManifest = null;
    state.timelineClips = [];
    state.timelineDirty = false;
    state.timelineRendered = false;
    state.audioMixManifest = null;
    state.audioMixClips = [];
    state.audioMixDirty = false;
    state.audioMixLevels = { voice: 0, music: 0, sfx: 0 };
    state.sourceAudioEnabled = true;
    state.sourceAudioDenoise = false;
    state.visualEffectsManifest = null;
    state.visualTransitions = [];
    state.visualTitles = [];
    state.visualOverlays = [];
    state.visualEffectsDirty = false;
    state.subtitleManifest = null;
    $("timeline-subtitles-download").hidden = true;
    $("timeline-subtitles-download").removeAttribute("href");
    $("timeline-subtitles-state").textContent = "لم تُنشأ ترجمة لهذا الخط الزمني بعد.";
    const preview = $("timeline-preview");
    if (preview) {
      preview.pause();
      preview.removeAttribute("src");
      preview.load();
    }
    if ($("timeline-preview-wrap")) $("timeline-preview-wrap").hidden = true;
    if ($("timeline-download")) {
      $("timeline-download").hidden = true;
      $("timeline-download").removeAttribute("href");
    }
    renderTimelineEditor();
    renderAudioMixEditor();
    renderVisualEffectsEditor();
  }

  async function loadProjectTimeline(projectId) {
    resetTimeline();
    if (!projectId) return;
    try {
      const manifest = await api(`/v1/projects/${encodeURIComponent(projectId)}/timeline`);
      if (manifest.project_id !== projectId || !Array.isArray(manifest.clips)) {
        throw new Error("بيانات الخط الزمني لا تخص المشروع المحدد أو أن بنيتها غير صالحة.");
      }
      state.timelineManifest = manifest;
      const savedFps = String(Number(manifest.fps));
      $("timeline-fps").value = ["24", "30", "60"].includes(savedFps) ? savedFps : "30";
      state.timelineClips = manifest.clips.map((clip) => ({
        clip_id: clip.clip_id,
        asset_id: clip.asset_id,
        source_start_seconds: Number(clip.source_start_seconds),
        duration_seconds: Number(clip.duration_seconds)
      }));
    } catch (error) {
      if (error.status !== 404) {
        notice(`تعذّر تحميل الخط الزمني: ${error.message}`, "error");
      }
    }
    renderTimelineEditor();
  }

  function audioMixEndpoint() {
    return state.project
      ? "/v1/projects/" + encodeURIComponent(state.project.id) + "/audio-mix"
      : null;
  }

  function visualEffectsEndpoint() {
    return state.project
      ? "/v1/projects/" + encodeURIComponent(state.project.id) + "/visual-effects"
      : null;
  }

  async function loadProjectVisualEffects(projectId) {
    state.visualEffectsManifest = null;
    state.visualTransitions = [];
    state.visualTitles = [];
    state.visualOverlays = [];
    state.visualEffectsDirty = false;
    if (!projectId || !state.timelineManifest) {
      renderVisualEffectsEditor();
      return;
    }
    try {
      const manifest = await api("/v1/projects/" + encodeURIComponent(projectId) + "/visual-effects");
      if (
        manifest.project_id !== projectId ||
        !Array.isArray(manifest.transitions) ||
        !Array.isArray(manifest.titles) ||
        !Array.isArray(manifest.overlays)
      ) {
        throw new Error("إعدادات المؤثرات لا تخص المشروع المحدد أو أن بنيتها غير صالحة.");
      }
      state.visualEffectsManifest = manifest;
      state.visualTransitions = manifest.transitions.map((item) => ({
        after_clip_id: item.after_clip_id,
        style: item.style,
        duration_seconds: Number(item.duration_seconds)
      }));
      state.visualTitles = manifest.titles.map((item) => ({
        ...item,
        start_seconds: Number(item.start_seconds),
        duration_seconds: Number(item.duration_seconds)
      }));
      state.visualOverlays = manifest.overlays.map((item) => ({
        ...item,
        start_seconds: Number(item.start_seconds),
        duration_seconds: Number(item.duration_seconds),
        width_ratio: Number(item.width_ratio),
        opacity: Number(item.opacity)
      }));
      state.visualEffectsDirty =
        Math.abs(Number(manifest.timeline_duration_seconds || 0) - timelineTotalDuration()) > 0.001;
    } catch (error) {
      if (error.status !== 404) {
        notice("تعذّر تحميل المؤثرات البصرية: " + error.message, "error");
      }
    }
    renderVisualEffectsEditor();
  }

  function audioSourceAssets() {
    return state.assets.filter((asset) =>
      asset.asset_type === "audio" ||
      (asset.asset_type === "video" &&
        (asset.probe?.streams || []).some((stream) => stream.codec_type === "audio"))
    );
  }

  async function loadProjectAudioMix(projectId) {
    state.audioMixManifest = null;
    state.audioMixClips = [];
    state.audioMixDirty = false;
    state.audioMixLevels = { voice: 0, music: 0, sfx: 0 };
    state.sourceAudioEnabled = true;
    state.sourceAudioDenoise = false;
    if (!projectId) {
      renderAudioMixEditor();
      return;
    }
    try {
      const manifest = await api("/v1/projects/" + encodeURIComponent(projectId) + "/audio-mix");
      if (manifest.project_id !== projectId || !Array.isArray(manifest.clips)) {
        throw new Error("إعدادات الصوت لا تخص المشروع المحدد أو أن بنيتها غير صالحة.");
      }
      state.audioMixManifest = manifest;
      state.audioMixClips = manifest.clips.map((clip) => ({
        clip_id: clip.clip_id,
        asset_id: clip.asset_id,
        role: clip.role,
        timeline_start_seconds: Number(clip.timeline_start_seconds),
        source_start_seconds: Number(clip.source_start_seconds),
        duration_seconds: Number(clip.duration_seconds),
        gain_db: Number(clip.gain_db ?? 0),
        fade_in_seconds: Number(clip.fade_in_seconds ?? 0),
        fade_out_seconds: Number(clip.fade_out_seconds ?? 0),
        denoise: Boolean(clip.denoise)
      }));
      state.audioMixLevels = {
        voice: Number(manifest.levels_db?.voice ?? 0),
        music: Number(manifest.levels_db?.music ?? 0),
        sfx: Number(manifest.levels_db?.sfx ?? 0)
      };
      state.sourceAudioEnabled = manifest.source_audio_enabled !== false;
      state.sourceAudioDenoise = Boolean(manifest.source_audio_denoise);
      state.audioMixDirty =
        Math.abs(Number(manifest.timeline_duration_seconds || 0) - timelineTotalDuration()) > 0.001;
      if (state.audioMixDirty) {
        $("audio-mix-help").textContent =
          "تغيّرت مدة الفيديو بعد آخر حفظ للصوت. راجع مواضع المسارات ثم احفظ إعدادات الصوت مجددًا.";
      }
    } catch (error) {
      if (error.status !== 404) {
        notice("تعذّر تحميل إعدادات الصوت: " + error.message, "error");
      }
    }
    renderAudioMixEditor();
  }

  function audioRoleLabel(role) {
    return ({ voice: "تعليق صوتي", music: "موسيقى", sfx: "مؤثر صوتي" })[role] || role;
  }

  function renderAudioMixEditor() {
    const source = $("audio-mix-source");
    const list = $("audio-mix-list");
    if (!source || !list) return;
    const available = audioSourceAssets();
    const previous = source.value;
    source.replaceChildren();
    if (!available.length) {
      source.add(new Option("ارفع ملفًا صوتيًا أو فيديو يحتوي على صوت", ""));
      source.disabled = true;
    } else {
      available.forEach((asset) => {
        const prefix = asset.asset_type === "audio" ? "صوت" : "فيديو بصوت";
        source.add(new Option(prefix + " · " + getAssetLabel(asset), asset.id));
      });
      source.value = available.some((asset) => asset.id === previous)
        ? previous
        : available[0].id;
      source.disabled = false;
    }

    ["voice", "music", "sfx"].forEach((role) => {
      const slider = $("audio-level-" + role);
      if (slider) slider.value = String(state.audioMixLevels[role] ?? 0);
      const output = $("audio-level-" + role + "-value");
      if (output) output.textContent = String(state.audioMixLevels[role] ?? 0) + " dB";
    });
    $("source-audio-enabled").checked = state.sourceAudioEnabled;
    $("source-audio-denoise").checked = state.sourceAudioDenoise;

    list.replaceChildren();
    if (!state.audioMixClips.length) {
      const empty = document.createElement("div");
      empty.className = "audio-mix-empty";
      empty.textContent =
        "لم تتم إضافة مسارات صوتية مستقلة بعد. يمكنك إبقاء صوت الفيديو الأصلي أو إضافة تعليق وموسيقى ومؤثرات.";
      list.append(empty);
    }

    state.audioMixClips.forEach((clip, index) => {
      const asset = state.assets.find((item) => item.id === clip.asset_id);
      const card = document.createElement("article");
      card.className = "audio-mix-card";
      card.dataset.audioClipId = clip.clip_id;
      const header = document.createElement("div");
      header.className = "audio-mix-card-header";
      const heading = document.createElement("strong");
      heading.textContent = String(index + 1).padStart(2, "0") + " · " + getAssetLabel(asset);
      const role = document.createElement("span");
      role.textContent = audioRoleLabel(clip.role);
      header.append(heading, role);

      const fields = document.createElement("div");
      fields.className = "audio-mix-card-fields";
      [
        { key: "role", label: "نوع المسار", type: "select" },
        { key: "timeline_start_seconds", label: "موضع البداية (ث)", type: "number", min: 0, max: 3600 },
        { key: "source_start_seconds", label: "بداية المصدر (ث)", type: "number", min: 0, max: 3600 },
        { key: "duration_seconds", label: "المدة (ث)", type: "number", min: 0.01, max: 3600 },
        { key: "gain_db", label: "مستوى المقطع (dB)", type: "number", min: -60, max: 12 },
        { key: "fade_in_seconds", label: "تلاشي الدخول (ث)", type: "number", min: 0, max: 30 },
        { key: "fade_out_seconds", label: "تلاشي الخروج (ث)", type: "number", min: 0, max: 30 },
        { key: "denoise", label: "خفض الضوضاء", type: "checkbox" }
      ].forEach((field) => {
        const wrap = document.createElement("label");
        wrap.className = "audio-mix-card-field";
        const caption = document.createElement("span");
        caption.textContent = field.label;
        let input;
        if (field.type === "select") {
          input = document.createElement("select");
          input.className = "control";
          [
            ["voice", "تعليق صوتي"],
            ["music", "موسيقى"],
            ["sfx", "مؤثر صوتي"]
          ].forEach((option) => input.add(new Option(option[1], option[0])));
          input.value = clip.role;
        } else if (field.type === "checkbox") {
          input = document.createElement("input");
          input.type = "checkbox";
          input.checked = Boolean(clip.denoise);
        } else {
          input = document.createElement("input");
          input.className = "control";
          input.type = "number";
          input.min = String(field.min);
          input.max = String(field.max);
          input.step = "0.1";
          input.value = String(clip[field.key]);
        }
        input.dataset.audioClipId = clip.clip_id;
        input.dataset.audioClipField = field.key;
        if (field.type === "checkbox") input.dataset.audioClipType = "checkbox";
        wrap.append(caption, input);
        fields.append(wrap);
      });

      const actions = document.createElement("div");
      actions.className = "audio-mix-card-actions";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.audioClipAction = "remove";
      remove.dataset.audioClipId = clip.clip_id;
      remove.textContent = "حذف المسار";
      actions.append(remove);
      card.append(header, fields, actions);
      list.append(card);
    });

    $("audio-mix-count").textContent = state.audioMixClips.length + " مسار صوتي";
    $("audio-mix-state").textContent = state.audioMixDirty ? "تغييرات غير محفوظة" : "تم الحفظ";
    $("audio-mix-save").disabled = !state.project || !state.audioMixDirty;
    $("audio-mix-add").disabled = !state.project || !available.length || !state.timelineClips.length;
    if (!state.audioMixDirty) {
      $("audio-mix-help").textContent =
        "احفظ إعدادات الصوت. تُطبّق عند إنشاء معاينة MP4 أو تصدير الفيديو.";
    }
  }

  function markAudioMixDirty() {
    state.audioMixDirty = true;
    state.timelineRendered = false;
    if ($("timeline-preview-wrap")) $("timeline-preview-wrap").hidden = true;
    if ($("timeline-download")) $("timeline-download").hidden = true;
    $("audio-mix-state").textContent = "تغييرات غير محفوظة";
    $("audio-mix-save").disabled = !state.project;
  }



  function renderVisualEffectsEditor() {
    const transitionsList = $("visual-transition-list");
    const titleList = $("visual-title-list");
    const overlayList = $("visual-overlay-list");
    if (!transitionsList || !titleList || !overlayList) return;

    const clipSelect = $("visual-transition-after");
    const selectedClip = clipSelect.value;
    clipSelect.replaceChildren();
    if (state.timelineClips.length < 2) {
      clipSelect.add(new Option("أضف مقطعين على الأقل", ""));
      clipSelect.disabled = true;
    } else {
      state.timelineClips.slice(0, -1).forEach((clip, index) => {
        const asset = state.assets.find((item) => item.id === clip.asset_id);
        clipSelect.add(new Option(
          (index + 1) + " ← " + getAssetLabel(asset),
          clip.clip_id
        ));
      });
      clipSelect.value = state.timelineClips.some((clip) => clip.clip_id === selectedClip)
        ? selectedClip
        : state.timelineClips[0].clip_id;
      clipSelect.disabled = false;
    }

    const imageSelect = $("visual-overlay-asset");
    const previousImage = imageSelect.value;
    imageSelect.replaceChildren();
    const images = state.assets.filter((asset) => asset.asset_type === "image");
    if (!images.length) {
      imageSelect.add(new Option("ارفع صورة PNG أو JPG أو WEBP أولًا", ""));
      imageSelect.disabled = true;
    } else {
      images.forEach((asset) => imageSelect.add(new Option(getAssetLabel(asset), asset.id)));
      imageSelect.value = images.some((asset) => asset.id === previousImage)
        ? previousImage
        : images[0].id;
      imageSelect.disabled = false;
    }

    transitionsList.replaceChildren();
    if (!state.visualTransitions.length) {
      const empty = document.createElement("p");
      empty.className = "visual-empty";
      empty.textContent = "لم تتم إضافة انتقالات بعد.";
      transitionsList.append(empty);
    }
    state.visualTransitions.forEach((item, index) => {
      const row = document.createElement("article");
      row.className = "visual-effect-card";
      const label = document.createElement("strong");
      const clipIndex = state.timelineClips.findIndex((clip) => clip.clip_id === item.after_clip_id);
      label.textContent = "انتقال بعد المقطع " + (clipIndex + 1);
      const details = document.createElement("span");
      details.textContent =
        (item.style === "flash_white" ? "وميض أبيض" : "تلاشي إلى الأسود") +
        " · " + item.duration_seconds + " ث";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.visualAction = "remove-transition";
      remove.dataset.visualIndex = String(index);
      remove.textContent = "حذف";
      row.append(label, details, remove);
      transitionsList.append(row);
    });

    titleList.replaceChildren();
    if (!state.visualTitles.length) {
      const empty = document.createElement("p");
      empty.className = "visual-empty";
      empty.textContent = "لا توجد عناوين أو نصوص ظاهرة على الفيديو.";
      titleList.append(empty);
    }
    state.visualTitles.forEach((item, index) => {
      const row = document.createElement("article");
      row.className = "visual-effect-card";
      const copy = document.createElement("div");
      const label = document.createElement("strong");
      label.textContent = item.text;
      const details = document.createElement("span");
      details.textContent = item.start_seconds + "–" +
        (item.start_seconds + item.duration_seconds).toFixed(1) + " ث · " +
        item.position + " · " + item.animation;
      copy.append(label, details);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.visualAction = "remove-title";
      remove.dataset.visualIndex = String(index);
      remove.textContent = "حذف";
      row.append(copy, remove);
      titleList.append(row);
    });

    overlayList.replaceChildren();
    if (!state.visualOverlays.length) {
      const empty = document.createElement("p");
      empty.className = "visual-empty";
      empty.textContent = "لا توجد صور أو شعارات مضافة فوق الفيديو.";
      overlayList.append(empty);
    }
    state.visualOverlays.forEach((item, index) => {
      const asset = state.assets.find((entry) => entry.id === item.asset_id);
      const row = document.createElement("article");
      row.className = "visual-effect-card";
      const copy = document.createElement("div");
      const label = document.createElement("strong");
      label.textContent = getAssetLabel(asset);
      const details = document.createElement("span");
      details.textContent = item.start_seconds + "–" +
        (item.start_seconds + item.duration_seconds).toFixed(1) + " ث · " +
        item.position + " · شفافية " + Math.round(item.opacity * 100) + "%";
      copy.append(label, details);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.visualAction = "remove-overlay";
      remove.dataset.visualIndex = String(index);
      remove.textContent = "حذف";
      row.append(copy, remove);
      overlayList.append(row);
    });

    $("visual-effects-state").textContent = state.visualEffectsDirty
      ? "تغييرات غير محفوظة"
      : "تم الحفظ";
    $("visual-effects-save").disabled = !state.project || !state.visualEffectsDirty;
    $("visual-transition-add").disabled =
      !state.project || state.timelineClips.length < 2 ||
      state.visualTransitions.length >= 99 ||
      state.visualTransitions.some((item) => item.after_clip_id === clipSelect.value);
    $("visual-title-add").disabled =
      !state.project || !state.timelineClips.length || state.visualTitles.length >= 20;
    $("visual-overlay-add").disabled =
      !state.project || !images.length || !state.timelineClips.length ||
      state.visualOverlays.length >= 20;
    $("visual-overlay-upload").disabled = !state.project || state.uploading;
    $("visual-effects-help").textContent = state.visualEffectsDirty
      ? "هناك تعديلات لم تُحفظ. احفظ المؤثرات قبل إنشاء معاينة MP4."
      : "تُحفظ المؤثرات مع المشروع وتُطبّق عند إنشاء معاينة MP4.";
  }

  function addVisualTransition() {
    const afterClipId = $("visual-transition-after").value;
    const style = $("visual-transition-style").value;
    const duration = Number($("visual-transition-duration").value);
    const index = state.timelineClips.findIndex((clip) => clip.clip_id === afterClipId);
    if (
      index < 0 || index >= state.timelineClips.length - 1 ||
      state.visualTransitions.some((item) => item.after_clip_id === afterClipId) ||
      !Number.isFinite(duration) || duration <= 0 || duration > 2
    ) {
      notice("اختر حدًا صالحًا بين مقطعين وحدد مدة انتقال صحيحة.", "error");
      return;
    }
    const first = Number(state.timelineClips[index].duration_seconds);
    const second = Number(state.timelineClips[index + 1].duration_seconds);
    const currentFirst = state.visualTransitions
      .filter((item) => item.after_clip_id === afterClipId)
      .reduce((sum, item) => sum + Number(item.duration_seconds), 0);
    const previousClipId = index > 0 ? state.timelineClips[index - 1].clip_id : null;
    const nextClipId = state.timelineClips[index + 1].clip_id;
    const previousBoundary = state.visualTransitions.find(
      (item) => item.after_clip_id === previousClipId
    );
    const nextBoundary = state.visualTransitions.find(
      (item) => item.after_clip_id === nextClipId
    );
    const usedOnFirst = previousBoundary ? Number(previousBoundary.duration_seconds) : 0;
    const usedOnSecond = nextBoundary ? Number(nextBoundary.duration_seconds) : 0;
    if (duration + usedOnFirst > first + 0.0001 ||
        duration + usedOnSecond > second + 0.0001 ||
        duration > first / 2 || duration > second / 2) {
      notice("مدة الانتقال كبيرة مقارنة بالمقطعين أو تتداخل مع انتقال مجاور.", "error");
      return;
    }
    state.visualTransitions.push({
      after_clip_id: afterClipId,
      style,
      duration_seconds: duration
    });
    markVisualEffectsDirty();
    notice("تمت إضافة الانتقال. احفظ المؤثرات لتطبيقه عند التصدير.", "success");
  }

  function addVisualTitle() {
    const text = $("visual-title-text").value.trim();
    const start = Number($("visual-title-start").value);
    const duration = Number($("visual-title-duration").value);
    const fontSize = Number($("visual-title-size").value);
    if (
      !text || text.length > 280 || !Number.isFinite(start) ||
      !Number.isFinite(duration) || !Number.isFinite(fontSize) ||
      start < 0 || duration <= 0 || fontSize < 18 || fontSize > 120 ||
      start + duration > timelineTotalDuration() + 0.001 ||
      state.visualTitles.length >= 20
    ) {
      notice("تحقق من نص العنوان وموضعه ومدته وحجم الخط.", "error");
      return;
    }
    state.visualTitles.push({
      title_id: uuid(),
      text,
      start_seconds: start,
      duration_seconds: duration,
      font_size: fontSize,
      color: $("visual-title-color").value,
      position: $("visual-title-position").value,
      animation: $("visual-title-animation").value,
      background: $("visual-title-background").checked
    });
    $("visual-title-text").value = "";
    markVisualEffectsDirty();
    notice("أُضيف العنوان إلى الفيديو. احفظ المؤثرات قبل التصدير.", "success");
  }

  function addVisualOverlay() {
    const assetId = $("visual-overlay-asset").value;
    const asset = state.assets.find((item) => item.id === assetId && item.asset_type === "image");
    const start = Number($("visual-overlay-start").value);
    const duration = Number($("visual-overlay-duration").value);
    const width = Number($("visual-overlay-width").value) / 100;
    const opacity = Number($("visual-overlay-opacity").value) / 100;
    if (
      !asset || !Number.isFinite(start) || !Number.isFinite(duration) ||
      !Number.isFinite(width) || !Number.isFinite(opacity) ||
      start < 0 || duration <= 0 || start + duration > timelineTotalDuration() + 0.001 ||
      width < 0.05 || width > 0.6 || opacity < 0.1 || opacity > 1 ||
      state.visualOverlays.length >= 20
    ) {
      notice("تحقق من الصورة والتوقيت والحجم والشفافية.", "error");
      return;
    }
    state.visualOverlays.push({
      overlay_id: uuid(),
      asset_id: asset.id,
      start_seconds: start,
      duration_seconds: duration,
      width_ratio: width,
      opacity,
      position: $("visual-overlay-position").value,
      animation: $("visual-overlay-animation").value
    });
    markVisualEffectsDirty();
    notice("أُضيفت الصورة أو العلامة المائية إلى الفيديو.", "success");
  }

  function handleVisualEffectAction(event) {
    const button = event.target.closest("[data-visual-action]");
    if (!button) return;
    const index = Number(button.dataset.visualIndex);
    const action = button.dataset.visualAction;
    if (!Number.isInteger(index) || index < 0) return;
    if (action === "remove-transition") state.visualTransitions.splice(index, 1);
    else if (action === "remove-title") state.visualTitles.splice(index, 1);
    else if (action === "remove-overlay") state.visualOverlays.splice(index, 1);
    else return;
    markVisualEffectsDirty();
  }

  async function saveProjectVisualEffects() {
    if (!state.project) return false;
    if (state.timelineDirty) {
      const savedTimeline = await saveProjectTimeline();
      if (!savedTimeline) return false;
    }
    const button = $("visual-effects-save");
    button.disabled = true;
    try {
      const manifest = await api(visualEffectsEndpoint(), {
        method: "PUT",
        body: JSON.stringify({
          transitions: state.visualTransitions,
          titles: state.visualTitles,
          overlays: state.visualOverlays
        })
      });
      state.visualEffectsManifest = manifest;
      state.visualTransitions = manifest.transitions;
      state.visualTitles = manifest.titles;
      state.visualOverlays = manifest.overlays;
      state.visualEffectsDirty = false;
      renderVisualEffectsEditor();
      notice("تم حفظ الانتقالات والعناوين والطبقات البصرية داخل المشروع.", "success");
      return true;
    } catch (error) {
      notice("تعذّر حفظ المؤثرات البصرية: " + error.message, "error");
      return false;
    } finally {
      button.disabled = false;
      renderVisualEffectsEditor();
    }
  }

  async function uploadVisualOverlayImage(file) {
    if (!state.project) return;
    const allowed = [".png", ".jpg", ".jpeg", ".webp"];
    const name = file.name.toLowerCase();
    if (!allowed.some((extension) => name.endsWith(extension))) {
      notice("ارفع صورة PNG أو JPG أو WEBP.", "error");
      return;
    }
    state.uploading = true;
    renderVisualEffectsEditor();
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      const asset = await api("/v1/projects/" + encodeURIComponent(state.project.id) + "/assets", {
        method: "POST",
        body: form
      });
      const assets = await api("/v1/projects/" + encodeURIComponent(state.project.id) + "/assets");
      state.assets = Array.isArray(assets) ? assets : [];
      renderAssets();
      $("visual-overlay-asset").value = asset.id;
      notice("تم رفع الصورة وفحصها. يمكنك إضافتها كطبقة فوق الفيديو.", "success");
    } catch (error) {
      notice("تعذّر رفع الصورة: " + error.message, "error");
    } finally {
      state.uploading = false;
      renderVisualEffectsEditor();
    }
  }

  function addAudioMixClip() {
    const assetId = $("audio-mix-source").value;
    const asset = audioSourceAssets().find((item) => item.id === assetId);
    if (!state.project || !asset) {
      notice("اختر ملفًا يحتوي على مسار صوت صالح.", "error");
      return;
    }
    if (!state.timelineClips.length || !state.timelineManifest) {
      notice("أضف مقاطع الفيديو واحفظ الخط الزمني أولاً.", "error");
      return;
    }
    if (state.audioMixClips.length >= 100) {
      notice("الحد الأقصى هو 100 مسار صوتي.", "error");
      return;
    }
    const timelineStart = Number($("audio-mix-timeline-start").value);
    const sourceStart = Number($("audio-mix-source-start").value);
    const duration = Number($("audio-mix-duration").value);
    const gain = Number($("audio-mix-gain").value);
    const fadeIn = Number($("audio-mix-fade-in").value);
    const fadeOut = Number($("audio-mix-fade-out").value);
    const sourceDuration = getAssetDuration(asset);
    const totalDuration = timelineTotalDuration();
    if (![timelineStart, sourceStart, duration, gain, fadeIn, fadeOut].every(Number.isFinite) ||
        timelineStart < 0 || sourceStart < 0 || duration <= 0 ||
        gain < -60 || gain > 12 || fadeIn < 0 || fadeOut < 0 ||
        fadeIn + fadeOut > duration ||
        timelineStart + duration > totalDuration + 0.001 ||
        (sourceDuration !== null && sourceStart + duration > sourceDuration + 0.001)) {
      notice("تحقق من توقيت الصوت ومستواه والتلاشي؛ يجب أن يبقى المسار داخل مدة الفيديو ومصدر الصوت.", "error");
      return;
    }
    state.audioMixClips.push({
      clip_id: uuid(),
      asset_id: asset.id,
      role: $("audio-mix-role").value,
      timeline_start_seconds: timelineStart,
      source_start_seconds: sourceStart,
      duration_seconds: duration,
      gain_db: gain,
      fade_in_seconds: fadeIn,
      fade_out_seconds: fadeOut,
      denoise: $("audio-mix-denoise").checked
    });
    markAudioMixDirty();
    renderAudioMixEditor();
    notice("أُضيف المسار الصوتي. احفظ إعدادات الصوت لتطبيقه عند التصدير.", "success");
  }

  function handleAudioMixChange(event) {
    const target = event.target;
    const clipId = target.dataset.audioClipId;
    const field = target.dataset.audioClipField;
    if (clipId && field) {
      const clip = state.audioMixClips.find((item) => item.clip_id === clipId);
      if (!clip) return;
      if (target.dataset.audioClipType === "checkbox") {
        clip[field] = target.checked;
      } else if (field === "role") {
        clip[field] = target.value;
      } else {
        const value = Number(target.value);
        if (!Number.isFinite(value)) {
          notice("أدخل قيمة رقمية صالحة لمسار الصوت.", "error");
          renderAudioMixEditor();
          return;
        }
        clip[field] = value;
      }
      markAudioMixDirty();
      renderAudioMixEditor();
      return;
    }

    if (target.id && target.id.indexOf("audio-level-") === 0) {
      const role = target.id.replace("audio-level-", "");
      if (["voice", "music", "sfx"].includes(role)) {
        state.audioMixLevels[role] = Number(target.value);
        $("audio-level-" + role + "-value").textContent = target.value + " dB";
        markAudioMixDirty();
      }
    } else if (target.id === "source-audio-enabled") {
      state.sourceAudioEnabled = target.checked;
      markAudioMixDirty();
    } else if (target.id === "source-audio-denoise") {
      state.sourceAudioDenoise = target.checked;
      markAudioMixDirty();
    }
  }

  function handleAudioMixAction(event) {
    const button = event.target.closest("[data-audio-clip-action]");
    if (!button) return;
    if (button.dataset.audioClipAction === "remove") {
      state.audioMixClips = state.audioMixClips.filter(
        (clip) => clip.clip_id !== button.dataset.audioClipId
      );
      markAudioMixDirty();
      renderAudioMixEditor();
    }
  }

  async function saveProjectAudioMix() {
    if (!state.project) return false;
    if (state.timelineDirty) {
      const timelineSaved = await saveProjectTimeline();
      if (!timelineSaved) return false;
    }
    const button = $("audio-mix-save");
    button.disabled = true;
    try {
      const manifest = await api(audioMixEndpoint(), {
        method: "PUT",
        body: JSON.stringify({
          source_audio_enabled: state.sourceAudioEnabled,
          source_audio_denoise: state.sourceAudioDenoise,
          levels_db: state.audioMixLevels,
          clips: state.audioMixClips
        })
      });
      state.audioMixManifest = manifest;
      state.audioMixClips = manifest.clips;
      state.audioMixDirty = false;
      renderAudioMixEditor();
      notice("تم حفظ مزج الصوت والمزامنة داخل المشروع.", "success");
      return true;
    } catch (error) {
      notice("تعذّر حفظ إعدادات الصوت: " + error.message, "error");
      return false;
    } finally {
      button.disabled = false;
      renderAudioMixEditor();
    }
  }

  function syncAudioSourceFields() {
    const asset = audioSourceAssets().find((item) => item.id === $("audio-mix-source").value);
    const duration = getAssetDuration(asset);
    if (duration !== null) {
      $("audio-mix-duration").max = String(duration);
      if (Number($("audio-mix-duration").value) > duration) {
        $("audio-mix-duration").value = String(Math.min(5, duration));
      }
    } else {
      $("audio-mix-duration").max = "3600";
    }
  }

  async function uploadAudioAsset(file) {
    if (!state.project) {
      notice("اختر مشروعًا قبل رفع ملف الصوت.", "error");
      return;
    }
    const allowed = [".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"];
    const name = file.name.toLowerCase();
    if (!allowed.some((extension) => name.endsWith(extension))) {
      notice("صيغة الصوت غير مدعومة. استخدم WAV أو MP3 أو M4A أو AAC أو FLAC أو OGG أو OPUS.", "error");
      return;
    }
    state.uploading = true;
    setProjectControls();
    $("audio-upload-button").disabled = true;
    notice("جارٍ رفع ملف الصوت والتحقق من مساره الصوتي…", "info");
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      const asset = await api("/v1/projects/" + encodeURIComponent(state.project.id) + "/assets", {
        method: "POST",
        body: form
      });
      await reloadAssets();
      $("audio-mix-source").value = asset.id;
      syncAudioSourceFields();
      renderAudioMixEditor();
      notice("تم رفع ملف الصوت والتحقق منه بنجاح.", "success");
    } catch (error) {
      notice("تعذّر رفع ملف الصوت: " + error.message, "error");
    } finally {
      state.uploading = false;
      $("audio-upload-button").disabled = false;
      setProjectControls();
    }
  }

  function clearTranscriptionPreview() {
    state.transcriptionManifest = null;
    state.transcriptionAssetId = null;
    $("transcription-preview-panel").hidden = true;
    $("transcription-segments").replaceChildren();
    $("transcription-segment-count").textContent = "0 مقطع كلام";
    $("transcription-alignment-status").textContent = "حالة المحاذاة: غير معروفة";
    $("transcription-state").textContent = "لم يتم التفريغ";
  }

  function alignmentLabel(status) {
    if (status === "completed") return "محاذاة الكلمات مكتملة";
    if (status === "unavailable") return "محاذاة الكلمات غير متاحة؛ استخدم توقيت الجمل";
    return "توقيت الجمل متاح؛ محاذاة الكلمات غير مطلوبة";
  }

  function renderTranscriptionPreview() {
    const manifest = state.transcriptionManifest;
    const list = $("transcription-segments");
    list.replaceChildren();
    if (!manifest) {
      $("transcription-preview-panel").hidden = true;
      return;
    }
    const segments = Array.isArray(manifest.segments) ? manifest.segments : [];
    $("transcription-preview-panel").hidden = false;
    $("transcription-segment-count").textContent = `${segments.length} مقطع كلام · ${manifest.language === "ar" ? "العربية" : "English"}`;
    $("transcription-alignment-status").textContent = alignmentLabel(manifest.alignment_status);
    $("transcription-state").textContent = "تم التفريغ";
    segments.slice(0, 12).forEach((segment) => {
      const item = document.createElement("div");
      item.className = "transcription-segment";
      const timing = document.createElement("small");
      timing.textContent = `${formatDuration(Number(segment.start) || 0)} – ${formatDuration(Number(segment.end) || 0)}`;
      const copy = document.createElement("span");
      copy.textContent = String(segment.text || "").trim();
      item.append(timing, copy);
      list.append(item);
    });
    if (segments.length > 12) {
      const more = document.createElement("p");
      more.className = "transcription-more";
      more.textContent = `و${segments.length - 12} مقطع إضافي محفوظ في المشروع.`;
      list.append(more);
    }
  }

  async function loadAssetTranscription(assetId) {
    clearTranscriptionPreview();
    if (!state.project || !assetId) return;
    const asset = state.assets.find((item) => item.id === assetId && item.asset_type === "video");
    if (!asset) return;
    state.transcriptionAssetId = assetId;
    $("transcription-state").textContent = "جارٍ تحميل التفريغ المحفوظ…";
    try {
      const manifest = await api(
        `/v1/projects/${encodeURIComponent(state.project.id)}/assets/${encodeURIComponent(assetId)}/transcription`
      );
      if (!Array.isArray(manifest.segments) || !["ar", "en"].includes(manifest.language)) {
        throw new Error("ملف التفريغ المحفوظ غير صالح.");
      }
      state.transcriptionManifest = manifest;
      $("transcription-language").value = manifest.language;
      $("transcription-align").checked = manifest.alignment_status === "completed";
      renderTranscriptionPreview();
    } catch (error) {
      if (error.status === 404) {
        $("transcription-state").textContent = "لم يتم التفريغ بعد";
      } else {
        state.transcriptionAssetId = null;
        $("transcription-state").textContent = "تعذّر تحميل التفريغ";
        notice(`تعذّر تحميل التفريغ المحفوظ: ${error.message}`, "error");
      }
    }
    updateTimelineSummary();
  }

  async function transcribeSelectedAsset() {
    const assetId = $("timeline-source").value;
    const asset = state.assets.find((item) => item.id === assetId && item.asset_type === "video");
    if (!state.project || !asset) {
      notice("اختر فيديو من مصادر المشروع أولاً.", "error");
      return;
    }
    const button = $("transcription-run");
    button.disabled = true;
    button.textContent = "جارٍ تفريغ الكلام…";
    $("transcription-state").textContent = "قيد التفريغ";
    try {
      const manifest = await api(
        `/v1/projects/${encodeURIComponent(state.project.id)}/assets/${encodeURIComponent(assetId)}/transcription`,
        {
          method: "POST",
          body: JSON.stringify({
            language: $("transcription-language").value,
            model_name: "small",
            device: "cpu",
            align: $("transcription-align").checked
          })
        }
      );
      if (!Array.isArray(manifest.segments)) {
        throw new Error("لم يُرجع محرك التفريغ مقاطع كلام صالحة.");
      }
      state.transcriptionAssetId = assetId;
      state.transcriptionManifest = manifest;
      state.subtitleManifest = null;
      $("timeline-subtitles-download").hidden = true;
      $("timeline-subtitles-download").removeAttribute("href");
      $("timeline-subtitles-state").textContent =
        "تم تحديث التفريغ؛ أنشئ ملف SRT جديدًا ليعكس أحدث النص.";
      renderTranscriptionPreview();
      notice(
        `اكتمل تفريغ الكلام: ${manifest.segments.length} مقطع. ${alignmentLabel(manifest.alignment_status)}.`,
        "success"
      );
    } catch (error) {
      $("transcription-state").textContent = state.transcriptionManifest
        ? "تعذّر التحديث؛ التفريغ المحفوظ السابق ما زال متاحًا"
        : "فشل التفريغ";
      notice(`تعذّر تفريغ الصوت: ${error.message}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = "تفريغ صوت الفيديو الحالي";
      updateTimelineSummary();
    }
  }

  async function loadTimelineSubtitleStatus(projectId) {
    state.subtitleManifest = null;
    $("timeline-subtitles-download").hidden = true;
    $("timeline-subtitles-download").removeAttribute("href");
    $("timeline-subtitles-state").textContent = "لم تُنشأ ترجمة لهذا الخط الزمني بعد.";
    if (!projectId) return;
    try {
      const manifest = await api(
        `/v1/projects/${encodeURIComponent(projectId)}/timeline/subtitles/manifest`
      );
      if (manifest.project_id !== projectId || manifest.subtitle_format !== "srt") {
        throw new Error("ملف الترجمة لا يطابق المشروع الحالي.");
      }
      state.subtitleManifest = manifest;
      $("timeline-subtitles-download").href =
        `/v1/projects/${encodeURIComponent(projectId)}/timeline/subtitles?download=${Date.now()}`;
      $("timeline-subtitles-download").hidden = false;
      $("timeline-subtitles-state").textContent =
        `ملف SRT جاهز · ${manifest.caption_count} جملة · ${alignmentLabel(manifest.alignment_status)}.`;
    } catch (error) {
      if (error.status !== 404) {
        notice(`تعذّر تحميل حالة الترجمة: ${error.message}`, "error");
      }
    }
  }

  async function createTimelineSubtitles() {
    if (!state.project || !state.timelineClips.length) return;
    if (state.timelineDirty) {
      const saved = await saveProjectTimeline();
      if (!saved) return;
    }
    const button = $("timeline-subtitles-create");
    button.disabled = true;
    button.textContent = "جارٍ إنشاء الترجمة…";
    try {
      const manifest = await api(`${timelineEndpoint()}/subtitles`, {
        method: "POST",
        body: JSON.stringify({ require_alignment: false })
      });
      state.subtitleManifest = manifest;
      $("timeline-subtitles-download").href =
        `${timelineEndpoint()}/subtitles?download=${Date.now()}`;
      $("timeline-subtitles-download").hidden = false;
      $("timeline-subtitles-state").textContent =
        `اكتمل ملف SRT · ${manifest.caption_count} جملة · ${alignmentLabel(manifest.alignment_status)}.`;
      notice("تم إنشاء ملف الترجمة المتزامن مع الخط الزمني.", "success");
    } catch (error) {
      $("timeline-subtitles-state").textContent = "تعذّر إنشاء ملف الترجمة";
      notice(`تعذّر إنشاء الترجمة: ${error.message}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = "إنشاء ترجمة SRT للخط الزمني";
      updateTimelineSummary();
    }
  }

  function clearSceneSelection() {
    state.sceneManifest = null;
    state.sceneAssetId = null;
    $("scene-results-panel").hidden = true;
    $("scene-results").replaceChildren();
    $("scene-results-count").textContent = "0 مشهد";
    $("scene-detection-state").textContent = "لم يتم التحليل";
    $("scene-detection-help").textContent =
      "يُحفظ تحليل المشاهد مع ملف الفيديو داخل المشروع، ويمكن إعادة تحميله دون تشغيل التحليل مرة أخرى.";
    $("scene-detect").disabled = !state.project || !state.assets.some(
      (asset) => asset.id === $("timeline-source").value && asset.asset_type === "video"
    );
    updateSceneSelectionActions();
  }

  async function loadAssetScenes(assetId) {
    clearSceneSelection();
    if (!state.project || !assetId) return;
    const asset = state.assets.find((item) => item.id === assetId && item.asset_type === "video");
    if (!asset) return;
    state.sceneAssetId = assetId;
    $("scene-detection-state").textContent = "جارٍ تحميل التحليل المحفوظ…";
    $("scene-detect").disabled = true;
    try {
      const manifest = await api(
        `/v1/projects/${encodeURIComponent(state.project.id)}/assets/${encodeURIComponent(assetId)}/scenes`
      );
      if (manifest.project_id !== state.project.id ||
          manifest.asset_id !== assetId ||
          !Array.isArray(manifest.scenes)) {
        throw new Error("ملف المشاهد لا يطابق الفيديو الحالي.");
      }
      state.sceneManifest = manifest;
      renderSceneResults();
    } catch (error) {
      if (error.status === 404) {
        $("scene-detection-state").textContent = "لم يتم التحليل";
        $("scene-detection-help").textContent = "اضغط «تحليل المشاهد» لاكتشاف اللقطات في الفيديو المحدد.";
      } else {
        state.sceneAssetId = null;
        $("scene-detection-state").textContent = "تعذّر تحميل التحليل";
        notice(`تعذّر تحميل المشاهد المحفوظة: ${error.message}`, "error");
      }
    } finally {
      $("scene-detect").disabled = !state.project || !state.sceneAssetId;
    }
  }

  function renderSceneResults() {
    const manifest = state.sceneManifest;
    const list = $("scene-results");
    list.replaceChildren();
    if (!manifest) {
      $("scene-results-panel").hidden = true;
      updateSceneSelectionActions();
      return;
    }
    const scenes = manifest.scenes || [];
    $("scene-results-panel").hidden = false;
    $("scene-results-count").textContent = `${scenes.length} مشهد`;
    $("scene-detection-state").textContent = `تم تحليل ${scenes.length} مشهد`;
    $("scene-detection-help").textContent =
      "حدّد المشاهد التي تريدها ثم أضفها إلى الخط الزمني. سيُنشأ مقطع مستقل لكل مشهد مختار.";
    if (!scenes.length) {
      const empty = document.createElement("p");
      empty.className = "scene-results-empty";
      empty.textContent = "لم يعثر التحليل على مشاهد قابلة للاستخدام.";
      list.append(empty);
    }
    scenes.forEach((scene, index) => {
      const label = document.createElement("label");
      label.className = "scene-result-card";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.sceneSelect = String(scene.id);
      const body = document.createElement("span");
      body.className = "scene-result-copy";
      const title = document.createElement("strong");
      title.textContent = `المشهد ${String(scene.index || index + 1).padStart(2, "0")}`;
      const timing = document.createElement("small");
      const start = Number(scene.start_seconds);
      const duration = Number(scene.duration_seconds);
      timing.textContent = `من ${formatDuration(start)} · المدة ${formatDuration(duration)}`;
      body.append(title, timing);
      label.append(checkbox, body);
      list.append(label);
    });
    updateSceneSelectionActions();
  }

  function updateSceneSelectionActions() {
    const checkboxes = [...document.querySelectorAll("[data-scene-select]")];
    const selectedCount = checkboxes.filter((item) => item.checked).length;
    $("scene-add-selected").disabled =
      !state.sceneManifest || !state.sceneAssetId || selectedCount === 0;
    $("scene-add-selected").textContent = selectedCount
      ? `إضافة ${selectedCount} مشهد إلى الخط الزمني`
      : "إضافة المشاهد المختارة";
    $("scene-select-all").disabled = checkboxes.length === 0;
  }

  async function detectProjectScenes() {
    const assetId = $("timeline-source").value;
    const asset = state.assets.find((item) => item.id === assetId && item.asset_type === "video");
    if (!state.project || !asset) {
      notice("اختر فيديو من مصادر المشروع أولاً.", "error");
      return;
    }
    const threshold = Number($("scene-threshold").value);
    const minSceneLen = Number($("scene-min-length").value);
    if (!Number.isFinite(threshold) || threshold <= 0 || threshold > 100 ||
        !Number.isInteger(minSceneLen) || minSceneLen < 1 || minSceneLen > 1000) {
      notice("حساسية المشهد يجب أن تكون بين 1 و100، وأقل طول بين 1 و1000 إطار.", "error");
      return;
    }
    const button = $("scene-detect");
    button.disabled = true;
    button.textContent = "جارٍ تحليل المشاهد…";
    $("scene-detection-state").textContent = "قيد التحليل";
    try {
      const manifest = await api(
        `/v1/projects/${encodeURIComponent(state.project.id)}/assets/${encodeURIComponent(assetId)}/scenes`,
        {
          method: "POST",
          body: JSON.stringify({ threshold, min_scene_len: minSceneLen })
        }
      );
      if (manifest.project_id !== state.project.id ||
          manifest.asset_id !== assetId ||
          !Array.isArray(manifest.scenes)) {
        throw new Error("استجابة تحليل المشاهد لا تطابق الفيديو المحدد.");
      }
      state.sceneAssetId = assetId;
      state.sceneManifest = manifest;
      renderSceneResults();
      notice(`اكتمل تحليل الفيديو: ${manifest.scene_count} مشهد.`, "success");
    } catch (error) {
      state.sceneManifest = null;
      state.sceneAssetId = assetId;
      $("scene-results-panel").hidden = true;
      $("scene-detection-state").textContent = "فشل التحليل";
      notice(`تعذّر تحليل المشاهد: ${error.message}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = "تحليل المشاهد";
      updateSceneSelectionActions();
    }
  }

  function selectAllScenes() {
    document.querySelectorAll("[data-scene-select]").forEach((checkbox) => {
      checkbox.checked = true;
    });
    updateSceneSelectionActions();
  }

  function addSelectedScenesToTimeline() {
    if (!state.sceneManifest || !state.sceneAssetId) return;
    const selectedIds = new Set(
      [...document.querySelectorAll("[data-scene-select]:checked")].map((item) => item.dataset.sceneSelect)
    );
    const selectedScenes = (state.sceneManifest.scenes || [])
      .filter((scene) => selectedIds.has(String(scene.id)))
      .sort((a, b) => Number(a.index || 0) - Number(b.index || 0));
    if (!selectedScenes.length) {
      notice("حدّد مشهدًا واحدًا على الأقل.", "error");
      return;
    }
    const asset = state.assets.find((item) => item.id === state.sceneAssetId && item.asset_type === "video");
    if (!asset) {
      notice("لم يعد مصدر المشاهد موجودًا ضمن المشروع الحالي.", "error");
      clearSceneSelection();
      return;
    }
    if (state.timelineClips.length + selectedScenes.length > 100) {
      notice("ستتجاوز الإضافة حد 100 مقطع. احذف بعض المقاطع أولاً.", "error");
      return;
    }
    const additions = [];
    for (const scene of selectedScenes) {
      const start = Number(scene.start_seconds);
      const duration = Number(scene.duration_seconds);
      const sourceDuration = getAssetDuration(asset);
      if (!Number.isFinite(start) || start < 0 ||
          !Number.isFinite(duration) || duration <= 0 ||
          (sourceDuration !== null && start + duration > sourceDuration + 0.001)) {
        notice(`بيانات توقيت المشهد ${scene.index || scene.id} غير صالحة أو تتجاوز مدة المصدر.`, "error");
        return;
      }
      additions.push({
        clip_id: uuid(),
        asset_id: asset.id,
        source_start_seconds: start,
        duration_seconds: duration
      });
    }
    const nextDuration = timelineTotalDuration() +
      additions.reduce((total, clip) => total + clip.duration_seconds, 0);
    if (nextDuration > 3600) {
      notice("ستتجاوز الإضافة حد 60 دقيقة للخط الزمني.", "error");
      return;
    }
    state.timelineClips.push(...additions);
    markTimelineDirty();
    renderTimelineEditor();
    document.querySelectorAll("[data-scene-select]").forEach((checkbox) => {
      checkbox.checked = false;
    });
    updateSceneSelectionActions();
    notice(`أُضيف ${additions.length} مشهد إلى الخط الزمني. احفظ التغييرات لتثبيتها.`, "success");
  }

  function updateTimelineSummary() {
    const videos = state.assets.filter((asset) => asset.asset_type === "video");
    const total = timelineTotalDuration();
    $("timeline-clip-count").textContent = `${state.timelineClips.length} مقطع`;
    $("timeline-total-duration").textContent = `المدة الإجمالية: ${formatDuration(total)}`;
    $("timeline-empty").hidden = state.timelineClips.length > 0;
    $("timeline-state").textContent = state.timelineDirty
      ? "تغييرات غير محفوظة"
      : state.timelineRendered
        ? "المعاينة جاهزة"
        : state.timelineClips.length
          ? "تم الحفظ"
          : "لا توجد مقاطع";
    $("timeline-save").disabled = !state.project || !state.timelineDirty;
    $("timeline-render").disabled = !state.project || !state.timelineClips.length;
    $("timeline-subtitles-create").disabled = !state.project || !state.timelineClips.length;
    $("transcription-run").disabled = !state.project || !videos.some(
      (asset) => asset.id === $("timeline-source").value
    );
    $("timeline-add-clip").disabled = !state.project || !videos.length;
    $("timeline-source").disabled = !videos.length;
    $("timeline-help").textContent = state.timelineDirty
      ? "لديك تغييرات لم تُحفظ. احفظ الخط الزمني قبل تصدير المعاينة."
      : "يمكنك تعديل القص وإعادة ترتيب المقاطع. تبقى المعاينة السابقة صالحة حتى تغيّر الخط الزمني.";
  }

  function renderTimelineEditor() {
    const sourceSelect = $("timeline-source");
    const list = $("timeline-clip-list");
    if (!sourceSelect || !list) return;
    const videos = state.assets.filter((asset) => asset.asset_type === "video");
    const previousSource = sourceSelect.value;
    sourceSelect.replaceChildren();
    if (!videos.length) {
      sourceSelect.add(new Option("ارفع فيديوهات للمشروع أولاً", ""));
    } else {
      videos.forEach((asset) => sourceSelect.add(new Option(getAssetLabel(asset), asset.id)));
      sourceSelect.value = videos.some((asset) => asset.id === previousSource)
        ? previousSource
        : videos[0].id;
    }

    list.replaceChildren();
    state.timelineClips.forEach((clip, index) => {
      const asset = videos.find((item) => item.id === clip.asset_id);
      const card = document.createElement("article");
      card.className = "timeline-clip";
      card.dataset.clipId = clip.clip_id;

      const header = document.createElement("div");
      header.className = "timeline-clip-header";
      const heading = document.createElement("strong");
      heading.textContent = `${String(index + 1).padStart(2, "0")} · ${getAssetLabel(asset)}`;
      const position = document.createElement("span");
      position.textContent = `موضع ${index + 1}`;
      header.append(heading, position);

      const fields = document.createElement("div");
      fields.className = "timeline-clip-fields";
      [
        { key: "source_start_seconds", label: "بداية المصدر", min: 0, max: 3600 },
        { key: "duration_seconds", label: "مدة المقطع", min: 0.01, max: 3600 }
      ].forEach((field) => {
        const wrap = document.createElement("label");
        wrap.className = "timeline-clip-field";
        const caption = document.createElement("span");
        caption.textContent = `${field.label} (ث)`;
        const input = document.createElement("input");
        input.className = "control";
        input.type = "number";
        input.min = String(field.min);
        input.max = String(field.max);
        input.step = "0.01";
        input.value = String(clip[field.key]);
        input.dataset.clipId = clip.clip_id;
        input.dataset.clipField = field.key;
        input.inputMode = "decimal";
        wrap.append(caption, input);
        fields.append(wrap);
      });

      const actions = document.createElement("div");
      actions.className = "timeline-clip-actions";
      [
        { action: "up", label: "↑ للأعلى", disabled: index === 0 },
        { action: "down", label: "↓ للأسفل", disabled: index === state.timelineClips.length - 1 },
        { action: "remove", label: "حذف المقطع", disabled: false }
      ].forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = item.action === "remove" ? "timeline-remove" : "timeline-move";
        button.textContent = item.label;
        button.disabled = item.disabled;
        button.dataset.clipAction = item.action;
        button.dataset.clipId = clip.clip_id;
        actions.append(button);
      });

      card.append(header, fields, actions);
      list.append(card);
    });
    syncTimelineSourceFields();
    $("scene-detect").disabled = !state.project ||
      !videos.some((asset) => asset.id === sourceSelect.value);
    updateTimelineSummary();
    if (!state.workflow) setStepper(state.timelineClips.length ? 2 : 1);
  }

  function syncTimelineSourceFields() {
    const selected = $("timeline-source")?.value;
    const asset = state.assets.find((item) => item.id === selected && item.asset_type === "video");
    const duration = getAssetDuration(asset);
    $("timeline-in").max = duration === null ? "3600" : String(duration);
    $("timeline-duration").max = duration === null ? "3600" : String(duration);
    const currentDuration = Number($("timeline-duration").value);
    if (duration !== null &&
        (!Number.isFinite(currentDuration) || currentDuration <= 0 || currentDuration > duration)) {
      $("timeline-duration").value = String(Math.min(5, duration));
    }
    const currentStart = Number($("timeline-in").value);
    if (duration !== null &&
        (!Number.isFinite(currentStart) || currentStart < 0 || currentStart >= duration)) {
      $("timeline-in").value = "0";
    }
  }

  function markVisualEffectsDirty() {
    state.visualEffectsDirty = true;
    state.timelineRendered = false;
    if ($("timeline-preview-wrap")) $("timeline-preview-wrap").hidden = true;
    if ($("timeline-download")) $("timeline-download").hidden = true;
    renderVisualEffectsEditor();
  }

  function markTimelineDirty() {
    state.timelineDirty = true;
    state.visualEffectsDirty = true;
    state.subtitleManifest = null;
    $("timeline-subtitles-download").hidden = true;
    $("timeline-subtitles-download").removeAttribute("href");
    $("timeline-subtitles-state").textContent =
      "تغيّر الخط الزمني؛ أنشئ ملف SRT جديدًا بعد حفظ التعديلات.";
    if ($("timeline-preview-wrap")) $("timeline-preview-wrap").hidden = true;
    if ($("timeline-download")) $("timeline-download").hidden = true;
    state.timelineRendered = false;
    state.audioMixDirty = true;
    renderAudioMixEditor();
    renderVisualEffectsEditor();
    updateTimelineSummary();
  }

  function addTimelineClip() {
    const assetId = $("timeline-source").value;
    const asset = state.assets.find((item) => item.id === assetId && item.asset_type === "video");
    if (!asset) {
      notice("اختر فيديو صالحًا من مواد المشروع أولاً.", "error");
      return;
    }
    if (state.timelineClips.length >= 100) {
      notice("الحد الأقصى للخط الزمني هو 100 مقطع.", "error");
      return;
    }
    const sourceStart = Number($("timeline-in").value);
    const duration = Number($("timeline-duration").value);
    const sourceDuration = getAssetDuration(asset);
    if (!Number.isFinite(sourceStart) || sourceStart < 0 ||
        !Number.isFinite(duration) || duration <= 0) {
      notice("أدخل بداية مصدر غير سالبة ومدة أكبر من صفر.", "error");
      return;
    }
    if (sourceDuration !== null && sourceStart + duration > sourceDuration + 0.001) {
      notice("نهاية القص تتجاوز مدة الفيديو المصدر.", "error");
      return;
    }
    if (timelineTotalDuration() + duration > 3600) {
      notice("لا يمكن أن تتجاوز مدة الخط الزمني 60 دقيقة.", "error");
      return;
    }
    state.timelineClips.push({
      clip_id: uuid(),
      asset_id: asset.id,
      source_start_seconds: sourceStart,
      duration_seconds: duration
    });
    markTimelineDirty();
    renderTimelineEditor();
    notice("أُضيف المقطع إلى الخط الزمني. احفظ التعديلات لتثبيتها.", "success");
  }

  function handleTimelineClipChange(event) {
    const input = event.target.closest("[data-clip-field]");
    if (!input) return;
    const clip = state.timelineClips.find((item) => item.clip_id === input.dataset.clipId);
    if (!clip) return;
    const value = Number(input.value);
    const asset = state.assets.find((item) => item.id === clip.asset_id);
    const sourceDuration = getAssetDuration(asset);
    const field = input.dataset.clipField;
    const valid = Number.isFinite(value) &&
      (field === "source_start_seconds" ? value >= 0 : value > 0) &&
      value <= 3600;
    if (!valid) {
      notice("قيمة القص أو المدة غير صالحة.", "error");
      input.value = String(clip[field]);
      return;
    }
    const nextStart = field === "source_start_seconds" ? value : clip.source_start_seconds;
    const nextDuration = field === "duration_seconds" ? value : clip.duration_seconds;
    if (sourceDuration !== null && nextStart + nextDuration > sourceDuration + 0.001) {
      notice("نهاية القص تتجاوز مدة الفيديو المصدر.", "error");
      input.value = String(clip[field]);
      return;
    }
    if (field === "duration_seconds" &&
        timelineTotalDuration() - clip.duration_seconds + value > 3600) {
      notice("لا يمكن أن تتجاوز مدة الخط الزمني 60 دقيقة.", "error");
      input.value = String(clip[field]);
      return;
    }
    clip[field] = value;
    markTimelineDirty();
  }

  function handleTimelineClipAction(event) {
    const button = event.target.closest("[data-clip-action]");
    if (!button || button.disabled) return;
    const index = state.timelineClips.findIndex((item) => item.clip_id === button.dataset.clipId);
    if (index < 0) return;
    const action = button.dataset.clipAction;
    if (action === "remove") {
      state.timelineClips.splice(index, 1);
    } else if (action === "up" && index > 0) {
      [state.timelineClips[index - 1], state.timelineClips[index]] =
        [state.timelineClips[index], state.timelineClips[index - 1]];
    } else if (action === "down" && index < state.timelineClips.length - 1) {
      [state.timelineClips[index + 1], state.timelineClips[index]] =
        [state.timelineClips[index], state.timelineClips[index + 1]];
    }
    markTimelineDirty();
    renderTimelineEditor();
  }

  async function saveProjectTimeline() {
    if (!state.project) return false;
    const button = $("timeline-save");
    button.disabled = true;
    try {
      const manifest = await api(timelineEndpoint(), {
        method: "PUT",
        body: JSON.stringify({
          fps: Number($("timeline-fps").value),
          clips: state.timelineClips.map((clip) => ({
            clip_id: clip.clip_id,
            asset_id: clip.asset_id,
            source_start_seconds: clip.source_start_seconds,
            duration_seconds: clip.duration_seconds
          }))
        })
      });
      state.timelineManifest = manifest;
      state.timelineClips = manifest.clips.map((clip) => ({
        clip_id: clip.clip_id,
        asset_id: clip.asset_id,
        source_start_seconds: Number(clip.source_start_seconds),
        duration_seconds: Number(clip.duration_seconds)
      }));
      state.timelineDirty = false;
      renderTimelineEditor();
      notice("تم حفظ الخط الزمني في المشروع بنجاح.", "success");
      return true;
    } catch (error) {
      notice(`تعذّر حفظ الخط الزمني: ${error.message}`, "error");
      return false;
    } finally {
      button.disabled = false;
      updateTimelineSummary();
    }
  }

  async function renderProjectTimeline() {
    if (!state.project || !state.timelineClips.length) return;
    if (state.timelineDirty) {
      const saved = await saveProjectTimeline();
      if (!saved) return;
    }
    if (state.audioMixDirty) {
      const audioSaved = await saveProjectAudioMix();
      if (!audioSaved) return;
    }
    if (state.visualEffectsDirty) {
      const visualSaved = await saveProjectVisualEffects();
      if (!visualSaved) return;
    }
    const button = $("timeline-render");
    button.disabled = true;
    button.textContent = "جارٍ إنشاء المعاينة…";
    try {
      const [width, height] = $("timeline-resolution").value.split("x").map(Number);
      const result = await api(`${timelineEndpoint()}/render`, {
        method: "POST",
        body: JSON.stringify({
          width,
          height,
          fps: Number($("timeline-fps").value),
          crf: 22
        })
      });
      const downloadUrl = `${timelineEndpoint()}/rendered?preview=${Date.now()}`;
      $("timeline-preview").src = downloadUrl;
      $("timeline-preview-wrap").hidden = false;
      $("timeline-download").href = `${timelineEndpoint()}/rendered?download=${Date.now()}`;
      $("timeline-download").hidden = false;
      state.timelineRendered = true;
      $("timeline-state").textContent = "المعاينة جاهزة";
      notice(`اكتملت معاينة MP4 · ${formatBytes(result.size_bytes)} · ${result.clip_count} مقطع.`, "success");
    } catch (error) {
      notice(`تعذّر تصدير المعاينة: ${error.message}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = "إنشاء معاينة MP4";
      updateTimelineSummary();
    }
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
    await loadAssetScenes($("timeline-source").value);
    await loadAssetTranscription($("timeline-source").value);
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
    setStepper(3);
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
      setStepper(5);
      renderFinalVideo(workflow);
    } else {
      $("render-result").hidden = true;
      $("video-preview-wrap").hidden = true;
      if (workflow.status === "running" || workflow.status === "queued") setStepper(4);
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
    const qwenReady = Boolean(state.modelStatus?.models?.["qwen3.5-9b-q4_K_M"]?.ready);
    if (!qwenReady) {
      notice("لا يمكن إنشاء الخطة قبل تشغيل Ollama وتثبيت نموذج Qwen المحدد.", "error");
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
        transcribe_audio: !$("transcribe-audio").disabled && $("transcribe-audio").checked,
        enhance_audio: !$("enhance-audio").disabled && $("enhance-audio").checked,
        upscale_output: !$("upscale-video").disabled && $("upscale-video").checked,
        detection_prompt: $("detection-prompt").disabled
          ? null
          : $("detection-prompt").value.trim() || null,
        tracking_prompt: $("tracking-prompt").disabled
          ? null
          : $("tracking-prompt").value.trim() || null,
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
    if ((state.timelineDirty || state.audioMixDirty || state.visualEffectsDirty) &&
        !window.confirm("لديك تغييرات غير محفوظة في الخط الزمني أو الصوت أو المؤثرات. هل تريد تجاهلها وإنشاء مشروع جديد؟")) {
      return;
    }
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
      if ((state.timelineDirty || state.audioMixDirty || state.visualEffectsDirty) &&
          !window.confirm("لديك تغييرات غير محفوظة في الخط الزمني أو الصوت أو المؤثرات. هل تريد تجاهلها والانتقال؟")) {
        event.target.value = state.project?.id || "";
        return;
      }
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
    $("timeline-add-clip").addEventListener("click", addTimelineClip);
    $("timeline-clip-list").addEventListener("change", handleTimelineClipChange);
    $("timeline-clip-list").addEventListener("click", handleTimelineClipAction);
    $("timeline-save").addEventListener("click", saveProjectTimeline);
    $("timeline-render").addEventListener("click", renderProjectTimeline);
    $("visual-transition-after").addEventListener("change", renderVisualEffectsEditor);
    $("visual-transition-add").addEventListener("click", addVisualTransition);
    $("visual-title-add").addEventListener("click", addVisualTitle);
    $("visual-overlay-add").addEventListener("click", addVisualOverlay);
    $("visual-effects-save").addEventListener("click", saveProjectVisualEffects);
    $("visual-transition-list").addEventListener("click", handleVisualEffectAction);
    $("visual-title-list").addEventListener("click", handleVisualEffectAction);
    $("visual-overlay-list").addEventListener("click", handleVisualEffectAction);
    $("visual-overlay-upload").addEventListener("click", () => $("visual-overlay-file").click());
    $("visual-overlay-file").addEventListener("change", (event) => {
      if (event.target.files?.[0]) void uploadVisualOverlayImage(event.target.files[0]);
      event.target.value = "";
    });
    $("audio-mix-add").addEventListener("click", addAudioMixClip);
    $("audio-mix-save").addEventListener("click", saveProjectAudioMix);
    $("audio-mix-list").addEventListener("change", handleAudioMixChange);
    $("audio-mix-list").addEventListener("click", handleAudioMixAction);
    $("audio-mix-source").addEventListener("change", syncAudioSourceFields);
    ["audio-level-voice", "audio-level-music", "audio-level-sfx",
      "source-audio-enabled", "source-audio-denoise"].forEach((id) => {
      $(id).addEventListener("input", handleAudioMixChange);
      $(id).addEventListener("change", handleAudioMixChange);
    });
    $("audio-upload-button").addEventListener("click", () => $("audio-file").click());
    $("audio-file").addEventListener("change", (event) => {
      if (event.target.files?.[0]) void uploadAudioAsset(event.target.files[0]);
      event.target.value = "";
    });
    window.addEventListener("beforeunload", (event) => {
      if (!state.timelineDirty && !state.audioMixDirty && !state.visualEffectsDirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
    $("transcription-run").addEventListener("click", transcribeSelectedAsset);
    $("timeline-subtitles-create").addEventListener("click", createTimelineSubtitles);
    $("scene-detect").addEventListener("click", detectProjectScenes);
    $("scene-results").addEventListener("change", updateSceneSelectionActions);
    $("scene-select-all").addEventListener("click", selectAllScenes);
    $("scene-add-selected").addEventListener("click", addSelectedScenesToTimeline);
    $("timeline-source").addEventListener("change", (event) => {
      $("timeline-in").value = "0";
      $("timeline-duration").value = "5";
      syncTimelineSourceFields();
      void loadAssetScenes(event.target.value);
      void loadAssetTranscription(event.target.value);
    });
    $("timeline-fps").addEventListener("change", () => {
      if (state.timelineClips.length) markTimelineDirty();
    });
    $("approve-workflow").addEventListener("click", approveWorkflow);
    $("cancel-workflow").addEventListener("click", cancelDraft);
    $("stop-workflow").addEventListener("click", cancelWorkflow);
    bindUpload();
    $("refresh-model-status").addEventListener("click", () => loadModelStatus());
    await healthCheck();
    void loadModelStatus();
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
