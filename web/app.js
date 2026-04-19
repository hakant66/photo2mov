const form = document.querySelector("#path-form");
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

function setStatus(message, variant = "") {
  statusNode.textContent = message;
  statusNode.className = `status ${variant}`.trim();
}

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

function percentFromScale(scale) {
  return String((Number(scale) * 100).toFixed(2)).replace(/\.00$/, "");
}

async function loadConfig() {
  try {
    const response = await fetch("/config");
    if (!response.ok) {
      return;
    }
    const config = await response.json();
    if (config.default_output_dir) {
      outputDirInput.value = config.default_output_dir;
    }
    if (config.default_duration_seconds) {
      durationSecondsInput.value = config.default_duration_seconds;
    }
    if (config.default_start_scale) {
      startScaleInput.value = percentFromScale(config.default_start_scale);
    }
    if (config.default_end_scale) {
      endScaleInput.value = percentFromScale(config.default_end_scale);
    }
  } catch {
    // Ignore config bootstrap failures and keep the form usable.
  }
}

sourcePathInput.addEventListener("input", () => {
  clearResult();
});

outputDirInput.addEventListener("input", () => {
  clearResult();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!sourcePathInput.value.trim()) {
    setStatus("Enter a source photo path before submitting.", "error");
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
  setStatus("Rendering movie from the local path. Large images can take a moment.");

  try {
    const response = await fetch("/generate", {
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
    showResult(data);
    setStatus("Movie created. Click the result below to open or play it.", "success");
  } catch (error) {
    setStatus(error.message || "Unexpected error.", "error");
  } finally {
    submitButton.disabled = false;
  }
});

loadConfig();
