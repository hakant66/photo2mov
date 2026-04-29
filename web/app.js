function setStatus(node, message, variant = "") {
  node.textContent = message;
  node.className = `status ${variant}`.trim();
}

function percentFromScale(scale) {
  return String((Number(scale) * 100).toFixed(2)).replace(/\.00$/, "");
}

function parentDirectoryFromPath(filePath) {
  const normalized = String(filePath || "").trim();
  if (!normalized) {
    return "";
  }
  const slashIndex = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  if (slashIndex <= 0) {
    return "";
  }
  return normalized.slice(0, slashIndex);
}

async function readErrorMessage(response, fallbackMessage) {
  try {
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    if (contentType.includes("application/json")) {
      const data = await response.json();
      if (data && typeof data.error === "string" && data.error.trim()) {
        return data.error;
      }
    } else {
      const text = (await response.text()).trim();
      if (text) {
        return text;
      }
    }
  } catch {
    // Fall back to default message below.
  }
  return fallbackMessage;
}

async function loadConfig(singleRunElements, batchElements, enhanceElements) {
  try {
    const response = await fetch("/config");
    if (!response.ok) {
      return;
    }
    const config = await response.json();

    if (singleRunElements) {
      if (config.default_output_dir) {
        singleRunElements.outputDirInput.value = config.default_output_dir;
      }
      if (config.default_duration_seconds) {
        singleRunElements.durationSecondsInput.value = config.default_duration_seconds;
      }
      if (config.default_start_scale) {
        singleRunElements.startScaleInput.value = percentFromScale(config.default_start_scale);
      }
      if (config.default_end_scale) {
        singleRunElements.endScaleInput.value = percentFromScale(config.default_end_scale);
      }
    }

    if (batchElements) {
      if (config.default_output_dir) {
        batchElements.mainDirInput.value = config.default_output_dir;
      }
      if (config.default_duration_seconds) {
        batchElements.durationSecondsInput.value = config.default_duration_seconds;
      }
      if (config.default_start_scale) {
        batchElements.startScaleInput.value = percentFromScale(config.default_start_scale);
      }
      if (config.default_end_scale) {
        batchElements.endScaleInput.value = percentFromScale(config.default_end_scale);
      }
    }

    if (enhanceElements) {
      if (config.default_output_dir) {
        enhanceElements.mainDirInput.value = config.default_output_dir;
      }
    }
  } catch {
    // Ignore config bootstrap failures and keep forms usable.
  }
}

function initSingleRunPage() {
  const form = document.querySelector("#path-form");
  if (!form) {
    return null;
  }

  const sourcePathInput = document.querySelector("#source-path");
  const outputDirInput = document.querySelector("#output-dir");
  const durationSecondsInput = document.querySelector("#duration-seconds");
  const startScaleInput = document.querySelector("#start-scale");
  const endScaleInput = document.querySelector("#end-scale");
  const statusNode = document.querySelector("#status");
  const submitButton = document.querySelector("#submit-button");
  const resultCard = document.querySelector("#result-card");
  const resultName = document.querySelector("#result-name");
  const resultSource = document.querySelector("#result-source");
  const resultPath = document.querySelector("#result-path");
  const openLink = document.querySelector("#open-link");
  const downloadLink = document.querySelector("#download-link");
  const resultPlayer = document.querySelector("#result-player");
  setStatus(statusNode, "");

  function clearResult() {
    resultCard.hidden = true;
    resultName.textContent = "";
    resultSource.textContent = "";
    resultPath.textContent = "";
    openLink.href = "#";
    downloadLink.href = "#";
    downloadLink.removeAttribute("download");
    resultPlayer.pause();
    resultPlayer.removeAttribute("src");
    resultPlayer.load();
  }

  function showResult(payload) {
    resultName.textContent = payload.file_name;
    resultSource.textContent = `Source: ${payload.source_path}`;
    resultPath.textContent = payload.full_path;
    openLink.href = payload.file_url;
    downloadLink.href = payload.download_url;
    downloadLink.download = payload.file_name;
    resultPlayer.src = payload.file_url;
    resultPlayer.load();
    resultCard.hidden = false;
  }

  sourcePathInput.addEventListener("input", () => {
    const parentDir = parentDirectoryFromPath(sourcePathInput.value);
    if (parentDir) {
      outputDirInput.value = parentDir;
    }
    clearResult();
  });

  outputDirInput.addEventListener("input", () => {
    clearResult();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!sourcePathInput.value.trim()) {
      setStatus(statusNode, "Enter a source photo path before submitting.", "error");
      return;
    }

    const payload = {
      source_path: sourcePathInput.value.trim(),
      output_dir: outputDirInput.value.trim(),
      duration_seconds: Number(durationSecondsInput.value),
      start_scale: Number(startScaleInput.value) / 100,
      end_scale: Number(endScaleInput.value) / 100,
    };

    submitButton.disabled = true;
    clearResult();
    setStatus(statusNode, "Rendering movie from the local path. Large images can take a moment.");

    try {
      const response = await fetch("/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, `Request failed (HTTP ${response.status}).`);
        throw new Error(message);
      }

      const data = await response.json();
      showResult(data);
      setStatus(statusNode, "Movie created. Click the result below to open or play it.", "success");
    } catch (error) {
      setStatus(statusNode, error.message || "Unexpected error.", "error");
    } finally {
      submitButton.disabled = false;
    }
  });

  return {
    outputDirInput,
    durationSecondsInput,
    startScaleInput,
    endScaleInput,
  };
}

function initBatchRunPage() {
  const form = document.querySelector("#batch-form");
  if (!form) {
    return null;
  }

  const mainDirInput = document.querySelector("#batch-main-dir");
  const subfoldersInput = document.querySelector("#batch-subfolders");
  const durationSecondsInput = document.querySelector("#batch-duration-seconds");
  const startScaleInput = document.querySelector("#batch-start-scale");
  const endScaleInput = document.querySelector("#batch-end-scale");
  const statusNode = document.querySelector("#batch-status");
  const logNode = document.querySelector("#batch-log");
  const submitButton = document.querySelector("#batch-submit-button");
  const resultCard = document.querySelector("#batch-result-card");
  const resultSummary = document.querySelector("#batch-result-summary");
  const resultMainDir = document.querySelector("#batch-result-main-dir");
  const successList = document.querySelector("#batch-success-list");
  const failureList = document.querySelector("#batch-failure-list");
  let currentJobId = "";
  let lastLogSeq = 0;
  let pollingTimer = null;

  function clearBatchResult() {
    resultCard.hidden = true;
    resultSummary.textContent = "";
    resultMainDir.textContent = "";
    successList.innerHTML = "";
    failureList.innerHTML = "";
  }

  function clearLog() {
    if (logNode) {
      logNode.textContent = "";
    }
  }

  function appendLogLine(text) {
    if (!logNode) {
      return;
    }
    logNode.textContent = `${logNode.textContent}${text}\n`;
    logNode.scrollTop = logNode.scrollHeight;
  }

  function stopPolling() {
    if (pollingTimer) {
      window.clearInterval(pollingTimer);
      pollingTimer = null;
    }
  }

  function appendListItem(targetList, text, link) {
    const item = document.createElement("li");
    if (link) {
      const anchor = document.createElement("a");
      anchor.href = link;
      anchor.target = "_blank";
      anchor.rel = "noopener";
      anchor.textContent = text;
      item.appendChild(anchor);
    } else {
      item.textContent = text;
    }
    targetList.appendChild(item);
  }

  function showBatchResult(payload) {
    const requested = Number(payload.requested_count || 0);
    const processed = Number(payload.processed_count || 0);
    const generated = Number(payload.generated_count || 0);
    const failed = Number(payload.failed_count || 0);

    resultSummary.textContent = `${processed}/${requested} processed • ${generated} generated • ${failed} failed`;
    resultMainDir.textContent = `Main folder: ${payload.main_dir}`;

    successList.innerHTML = "";
    for (const item of payload.generated || []) {
      appendListItem(successList, `${item.subfolder} -> ${item.file_name}`, item.file_url);
    }
    if (!successList.children.length) {
      appendListItem(successList, "No files generated.");
    }

    failureList.innerHTML = "";
    for (const item of payload.failed || []) {
      appendListItem(failureList, `${item.subfolder}: ${item.error}`);
    }
    if (!failureList.children.length) {
      appendListItem(failureList, "No failures.");
    }

    resultCard.hidden = false;
  }

  function consumeLogs(logs) {
    for (const entry of logs || []) {
      const timestamp = entry.timestamp || "";
      const message = entry.message || "";
      appendLogLine(`[${timestamp}] ${message}`);
    }
  }

  function handleStatusPayload(payload) {
    consumeLogs(payload.logs || []);
    lastLogSeq = Number(payload.last_log_seq || lastLogSeq);
    showBatchResult(payload);

    const state = String(payload.state || "");
    if (state === "completed") {
      if (Number(payload.failed_count || 0) > 0) {
        setStatus(statusNode, "Batch run completed with errors. Check the log and summary below.", "error");
      } else {
        setStatus(statusNode, "Batch run completed successfully.", "success");
      }
      submitButton.disabled = false;
      stopPolling();
    } else if (state === "failed") {
      setStatus(statusNode, "Batch run failed. Check the log below.", "error");
      submitButton.disabled = false;
      stopPolling();
    }
  }

  async function pollBatchStatus() {
    if (!currentJobId) {
      return;
    }
    try {
      const response = await fetch(`/batch-generate/status?job_id=${encodeURIComponent(currentJobId)}&since_seq=${lastLogSeq}`);
      if (!response.ok) {
        const message = await readErrorMessage(response, `Status request failed (HTTP ${response.status}).`);
        throw new Error(message);
      }
      const payload = await response.json();
      handleStatusPayload(payload);
    } catch (error) {
      setStatus(statusNode, error.message || "Unexpected polling error.", "error");
      submitButton.disabled = false;
      stopPolling();
    }
  }

  setStatus(statusNode, "");
  clearLog();

  mainDirInput.addEventListener("input", clearBatchResult);
  subfoldersInput.addEventListener("input", clearBatchResult);
  mainDirInput.addEventListener("input", clearLog);
  subfoldersInput.addEventListener("input", clearLog);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!mainDirInput.value.trim()) {
      setStatus(statusNode, "Enter a main folder path before submitting.", "error");
      return;
    }
    if (!subfoldersInput.value.trim()) {
      setStatus(statusNode, "Enter at least one subfolder name before submitting.", "error");
      return;
    }

    const payload = {
      main_dir: mainDirInput.value.trim(),
      subfolders: subfoldersInput.value.trim(),
      duration_seconds: Number(durationSecondsInput.value),
      start_scale: Number(startScaleInput.value) / 100,
      end_scale: Number(endScaleInput.value) / 100,
    };

    submitButton.disabled = true;
    stopPolling();
    currentJobId = "";
    lastLogSeq = 0;
    clearBatchResult();
    clearLog();
    setStatus(statusNode, "Starting batch run. Progress will appear below.");

    try {
      const response = await fetch("/batch-generate/start", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, `Request failed (HTTP ${response.status}).`);
        throw new Error(message);
      }

      const data = await response.json();
      currentJobId = String(data.job_id || "");
      if (!currentJobId) {
        throw new Error("Server did not return a job id.");
      }
      handleStatusPayload(data);
      if (!pollingTimer) {
        pollingTimer = window.setInterval(pollBatchStatus, 1000);
      }
      setStatus(statusNode, "Batch run is in progress. Watch logs for live updates.");
    } catch (error) {
      setStatus(statusNode, error.message || "Unexpected error.", "error");
      submitButton.disabled = false;
      stopPolling();
    }
  });

  return {
    mainDirInput,
    durationSecondsInput,
    startScaleInput,
    endScaleInput,
  };
}

function initEnhancePage() {
  const form = document.querySelector("#enhance-form");
  if (!form) {
    return null;
  }

  const mainDirInput = document.querySelector("#enhance-main-dir");
  const maxImagesInput = document.querySelector("#enhance-max-images");
  const resolutionInput = document.querySelector("#enhance-resolution");
  const statusNode = document.querySelector("#enhance-status");
  const submitButton = document.querySelector("#enhance-submit-button");
  const stopButton = document.querySelector("#enhance-stop-button");
  const logNode = document.querySelector("#enhance-log");
  const resultCard = document.querySelector("#enhance-result-card");
  const resultSummary = document.querySelector("#enhance-result-summary");
  const resultMainDir = document.querySelector("#enhance-result-main-dir");
  const successList = document.querySelector("#enhance-success-list");
  const failureList = document.querySelector("#enhance-failure-list");
  let currentJobId = "";
  let lastLogSeq = 0;
  let pollingTimer = null;

  function clearResult() {
    resultCard.hidden = true;
    resultSummary.textContent = "";
    resultMainDir.textContent = "";
    successList.innerHTML = "";
    failureList.innerHTML = "";
  }

  function clearLog() {
    logNode.textContent = "";
  }
  setStatus(statusNode, "");
  clearLog();

  function appendLogLine(text) {
    logNode.textContent = `${logNode.textContent}${text}\n`;
    logNode.scrollTop = logNode.scrollHeight;
  }

  function setRunning(isRunning) {
    submitButton.disabled = isRunning;
    stopButton.disabled = !isRunning;
  }

  function stopPolling() {
    if (pollingTimer) {
      window.clearInterval(pollingTimer);
      pollingTimer = null;
    }
  }

  function appendListItem(targetList, text, link) {
    const item = document.createElement("li");
    if (link) {
      const anchor = document.createElement("a");
      anchor.href = link;
      anchor.target = "_blank";
      anchor.rel = "noopener";
      anchor.textContent = text;
      item.appendChild(anchor);
    } else {
      item.textContent = text;
    }
    targetList.appendChild(item);
  }

  function renderResult(payload) {
    const planned = Number(payload.planned_count || 0);
    const processed = Number(payload.processed_count || 0);
    const generated = Number(payload.generated_count || 0);
    const failed = Number(payload.failed_count || 0);

    resultSummary.textContent = `${processed}/${planned} processed • ${generated} enhanced • ${failed} failed`;
    resultMainDir.textContent = `Main: ${payload.main_dir} • Resolution: ${payload.resolution || "2000x2000"} • Requested: ${payload.requested_count} • Discovered: ${payload.discovered_count} • Planned: ${payload.planned_count}`;

    successList.innerHTML = "";
    for (const item of payload.generated || []) {
      appendListItem(successList, `${item.file_name} <- ${item.source_path}`, item.file_url);
    }
    if (!successList.children.length) {
      appendListItem(successList, "No images found to enhance.");
    }

    failureList.innerHTML = "";
    for (const item of payload.failed || []) {
      appendListItem(failureList, `${item.source_path}: ${item.error}`);
    }
    if (!failureList.children.length) {
      appendListItem(failureList, "No failures.");
    }

    resultCard.hidden = false;
  }

  function consumeLogs(logs) {
    for (const entry of logs || []) {
      const timestamp = entry.timestamp || "";
      const message = entry.message || "";
      appendLogLine(`[${timestamp}] ${message}`);
    }
  }

  function handleStatusPayload(payload) {
    consumeLogs(payload.logs || []);
    lastLogSeq = Number(payload.last_log_seq || lastLogSeq);
    renderResult(payload);

    const state = String(payload.state || "");
    if (state === "completed") {
      setStatus(statusNode, "Enhance batch completed.", "success");
      setRunning(false);
      stopPolling();
    } else if (state === "stopped") {
      setStatus(statusNode, "Enhance batch stopped.", "error");
      setRunning(false);
      stopPolling();
    } else if (state === "failed") {
      setStatus(statusNode, "Enhance batch failed.", "error");
      setRunning(false);
      stopPolling();
    }
  }

  async function pollStatus() {
    if (!currentJobId) {
      return;
    }
    try {
      const response = await fetch(`/batch-enhance/status?job_id=${encodeURIComponent(currentJobId)}&since_seq=${lastLogSeq}`);
      if (!response.ok) {
        const data = await response.json().catch(() => ({ error: "Unable to fetch status." }));
        throw new Error(data.error || "Unable to fetch status.");
      }
      const payload = await response.json();
      handleStatusPayload(payload);
    } catch (error) {
      setStatus(statusNode, error.message || "Unexpected error while polling.", "error");
      setRunning(false);
      stopPolling();
    }
  }

  mainDirInput.addEventListener("input", () => {
    clearResult();
    clearLog();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!mainDirInput.value.trim()) {
      setStatus(statusNode, "Enter a main folder path before submitting.", "error");
      return;
    }
    if (!maxImagesInput.value.trim() || Number(maxImagesInput.value) <= 0) {
      setStatus(statusNode, "Enter a valid image count greater than zero.", "error");
      return;
    }
    if (!resolutionInput.value.trim()) {
      setStatus(statusNode, "Enter a resolution in WIDTHxHEIGHT format.", "error");
      return;
    }

    const payload = {
      main_dir: mainDirInput.value.trim(),
      max_images: Number(maxImagesInput.value),
      resolution: resolutionInput.value.trim(),
    };

    stopPolling();
    setRunning(true);
    clearResult();
    clearLog();
    setStatus(statusNode, "Starting enhance batch and preparing the plan...");

    try {
      const response = await fetch("/batch-enhance/start", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({ error: "Request failed." }));
        throw new Error(data.error || "Request failed.");
      }

      const data = await response.json();
      currentJobId = String(data.job_id || "");
      if (!currentJobId) {
        throw new Error("Server did not return a job id.");
      }
      lastLogSeq = 0;
      handleStatusPayload(data);
      if (!pollingTimer) {
        pollingTimer = window.setInterval(pollStatus, 1000);
      }
      setStatus(statusNode, "Enhance batch is running. Progress and logs are updating live.");
    } catch (error) {
      setStatus(statusNode, error.message || "Unexpected error.", "error");
      setRunning(false);
      stopPolling();
    }
  });

  stopButton.addEventListener("click", async () => {
    if (!currentJobId) {
      return;
    }
    stopButton.disabled = true;
    try {
      const response = await fetch("/batch-enhance/stop", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ job_id: currentJobId }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({ error: "Stop request failed." }));
        throw new Error(data.error || "Stop request failed.");
      }
      setStatus(statusNode, "Stop request sent. Waiting for the current item to finish.", "error");
      await pollStatus();
    } catch (error) {
      setStatus(statusNode, error.message || "Stop request failed.", "error");
      stopButton.disabled = false;
    }
  });

  return {
    mainDirInput,
  };
}

function initSingleEnhancePage() {
  const form = document.querySelector("#single-enhance-form");
  if (!form) {
    return null;
  }

  const sourcePathInput = document.querySelector("#single-enhance-source-path");
  const resolutionInput = document.querySelector("#single-enhance-resolution");
  const statusNode = document.querySelector("#single-enhance-status");
  const submitButton = document.querySelector("#single-enhance-submit-button");
  const resultCard = document.querySelector("#single-enhance-result-card");
  const resultName = document.querySelector("#single-enhance-result-name");
  const resultSource = document.querySelector("#single-enhance-result-source");
  const resultPath = document.querySelector("#single-enhance-result-path");
  const openLink = document.querySelector("#single-enhance-open-link");
  const downloadLink = document.querySelector("#single-enhance-download-link");
  const previewImage = document.querySelector("#single-enhance-preview");
  setStatus(statusNode, "");

  function clearResult() {
    resultCard.hidden = true;
    resultName.textContent = "";
    resultSource.textContent = "";
    resultPath.textContent = "";
    openLink.href = "#";
    downloadLink.href = "#";
    downloadLink.removeAttribute("download");
    previewImage.removeAttribute("src");
  }

  function showResult(payload) {
    resultName.textContent = payload.file_name;
    resultSource.textContent = `Source: ${payload.source_path}`;
    resultPath.textContent = `${payload.full_path} (${payload.resolution || "2000x2000"})`;
    openLink.href = payload.file_url;
    downloadLink.href = payload.download_url;
    downloadLink.download = payload.file_name;
    previewImage.src = payload.file_url;
    resultCard.hidden = false;
  }

  sourcePathInput.addEventListener("input", clearResult);
  resolutionInput.addEventListener("input", clearResult);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!sourcePathInput.value.trim()) {
      setStatus(statusNode, "Enter a source image path before submitting.", "error");
      return;
    }
    if (!resolutionInput.value.trim()) {
      setStatus(statusNode, "Enter a resolution in WIDTHxHEIGHT format.", "error");
      return;
    }

    submitButton.disabled = true;
    clearResult();
    setStatus(statusNode, "Enhancing source image. Please wait.");

    try {
      const response = await fetch("/enhance-single", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          source_path: sourcePathInput.value.trim(),
          resolution: resolutionInput.value.trim(),
        }),
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, `Request failed (HTTP ${response.status}).`);
        throw new Error(message);
      }

      const payload = await response.json();
      showResult(payload);
      setStatus(statusNode, "Photo enhanced successfully.", "success");
    } catch (error) {
      setStatus(statusNode, error.message || "Unexpected error.", "error");
    } finally {
      submitButton.disabled = false;
    }
  });

  return {};
}

function initAvifConvertPage() {
  const form = document.querySelector("#avif-convert-form");
  if (!form) {
    return null;
  }

  const sourcePathInput = document.querySelector("#avif-source-path");
  const statusNode = document.querySelector("#avif-convert-status");
  const submitButton = document.querySelector("#avif-convert-submit-button");
  const resultCard = document.querySelector("#avif-convert-result-card");
  const resultName = document.querySelector("#avif-convert-result-name");
  const resultSource = document.querySelector("#avif-convert-result-source");
  const resultPath = document.querySelector("#avif-convert-result-path");
  const successList = document.querySelector("#avif-convert-success-list");
  const failureList = document.querySelector("#avif-convert-failure-list");
  const openLink = document.querySelector("#avif-convert-open-link");
  const downloadLink = document.querySelector("#avif-convert-download-link");
  setStatus(statusNode, "");

  function clearResult() {
    resultCard.hidden = true;
    resultName.textContent = "";
    resultSource.textContent = "";
    resultPath.textContent = "";
    successList.innerHTML = "";
    failureList.innerHTML = "";
    openLink.href = "#";
    downloadLink.href = "#";
    downloadLink.removeAttribute("download");
  }

  function appendListItem(targetList, text, link) {
    const item = document.createElement("li");
    if (link) {
      const anchor = document.createElement("a");
      anchor.href = link;
      anchor.target = "_blank";
      anchor.rel = "noopener";
      anchor.textContent = text;
      item.appendChild(anchor);
    } else {
      item.textContent = text;
    }
    targetList.appendChild(item);
  }

  function showResult(payload) {
    const generated = payload.generated || [];
    const failed = payload.failed || [];
    resultName.textContent = `${generated.length} converted, ${failed.length} failed`;
    resultSource.textContent = `Source: ${payload.source_path}`;

    successList.innerHTML = "";
    for (const item of generated) {
      appendListItem(successList, `${item.source_path} -> ${item.file_name}`, item.file_url);
    }
    if (!successList.children.length) {
      appendListItem(successList, "No converted files.");
    }

    failureList.innerHTML = "";
    for (const item of failed) {
      appendListItem(failureList, `${item.source_path}: ${item.error}`);
    }
    if (!failureList.children.length) {
      appendListItem(failureList, "No failures.");
    }

    const first = generated[0];
    if (first) {
      resultPath.textContent = first.full_path;
      openLink.href = first.file_url;
      downloadLink.href = first.download_url;
      downloadLink.download = first.file_name;
    } else {
      resultPath.textContent = "";
      openLink.href = "#";
      downloadLink.href = "#";
      downloadLink.removeAttribute("download");
    }
    resultCard.hidden = false;
  }

  sourcePathInput.addEventListener("input", clearResult);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!sourcePathInput.value.trim()) {
      setStatus(statusNode, "Enter a source AVIF path before submitting.", "error");
      return;
    }

    submitButton.disabled = true;
    clearResult();
    setStatus(statusNode, "Converting AVIF image to JPG. Please wait.");

    try {
      const response = await fetch("/convert-avif-to-jpg", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          source_path: sourcePathInput.value.trim(),
        }),
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, `Request failed (HTTP ${response.status}).`);
        throw new Error(message);
      }

      const payload = await response.json();
      showResult(payload);
      if (Number(payload.failed_count || 0) > 0) {
        setStatus(statusNode, "Conversion completed with some failures.", "error");
      } else {
        setStatus(
          statusNode,
          "AVIF conversion completed successfully. Source .avif file(s) were removed after .jpg creation.",
          "success"
        );
      }
    } catch (error) {
      setStatus(statusNode, error.message || "Unexpected error.", "error");
    } finally {
      submitButton.disabled = false;
    }
  });

  return {};
}

const singleRunElements = initSingleRunPage();
const batchElements = initBatchRunPage();
const enhanceElements = initEnhancePage();
initSingleEnhancePage();
initAvifConvertPage();
loadConfig(singleRunElements, batchElements, enhanceElements);

function clearStartupUiMessages() {
  const statusNodes = document.querySelectorAll(".status");
  for (const node of statusNodes) {
    node.textContent = "";
    node.className = "status";
  }
  const logNodes = document.querySelectorAll("#batch-log, #enhance-log");
  for (const node of logNodes) {
    node.textContent = "";
  }
}

clearStartupUiMessages();
window.addEventListener("pageshow", () => {
  clearStartupUiMessages();
});
