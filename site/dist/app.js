const $ = (selector) => document.querySelector(selector);

const elements = {
  fileInput: $("#fileInput"),
  dropzone: $("#dropzone"),
  fileName: $("#fileName"),
  dropIcon: $("#dropIcon"),
  engineStatus: $("#engineStatus"),
  convertButton: $("#convertButton"),
  errorMessage: $("#errorMessage"),
  resultCanvas: $("#resultCanvas"),
  cellTooltip: $("#cellTooltip"),
  emptyState: $("#emptyState"),
  realStat: $("#realStat"),
  gridStat: $("#gridStat"),
  visibleStat: $("#visibleStat"),
  timeStat: $("#timeStat"),
  zoom: $("#zoom"),
  zoomValue: $("#zoomValue"),
  showGrid: $("#showGrid"),
  materialTotal: $("#materialTotal"),
  materialSearch: $("#materialSearch"),
  materialRows: $("#materialRows"),
  downloadClean: $("#downloadClean"),
  downloadMapped: $("#downloadMapped"),
  downloadCsv: $("#downloadCsv"),
  contrastToggle: $("#contrastToggle"),
  liveStatus: $("#liveStatus"),
  loadingOverlay: $("#loadingOverlay"),
  pixelWidth: $("#pixelWidth"),
  pixelWidthNumber: $("#pixelWidthNumber"),
  pixelWidthValue: $("#pixelWidthValue"),
  tolerance: $("#tolerance"),
  toleranceNumber: $("#toleranceNumber"),
  toleranceValue: $("#toleranceValue"),
  binSize: $("#binSize"),
  binSizeNumber: $("#binSizeNumber"),
  binSizeValue: $("#binSizeValue"),
  removeBackground: $("#removeBackground"),
  paletteMode: $("#paletteMode"),
};

const state = {
  apiReady: false,
  file: null,
  result: null,
  cleanedImage: null,
  mappedImage: null,
  cleanedPixelContext: null,
  colorLookup: new Map(),
  processing: false,
};

function announce(message) {
  elements.liveStatus.textContent = "";
  requestAnimationFrame(() => { elements.liveStatus.textContent = message; });
}

function showError(message) {
  elements.errorMessage.textContent = message;
  elements.errorMessage.hidden = false;
  announce(message);
}

function readableError(value, fallback) {
  if (typeof value === "string" && value.trim()) return value;
  if (Array.isArray(value)) {
    const messages = value.map((item) => readableError(item, "")).filter(Boolean);
    if (messages.length) return messages.join(" ");
  }
  if (value && typeof value === "object") {
    for (const key of ["message", "msg", "detail", "error"]) {
      const message = readableError(value[key], "");
      if (message) return message;
    }
  }
  return fallback;
}

function clearError() {
  elements.errorMessage.hidden = true;
  elements.errorMessage.textContent = "";
}

function updateConvertState() {
  elements.convertButton.disabled = !state.apiReady || !state.file || state.processing;
}

function bindRange(range, number, output, format = (value) => value) {
  const sync = (source, target) => {
    const minimum = Number(target.min || 0);
    const maximum = Number(target.max || 255);
    const value = Math.max(minimum, Math.min(maximum, Number(source.value) || 0));
    source.value = String(value);
    target.value = String(value);
    output.textContent = format(value);
  };
  range.addEventListener("input", () => sync(range, number));
  number.addEventListener("input", () => sync(number, range));
  sync(range, number);
}

bindRange(elements.pixelWidth, elements.pixelWidthNumber, elements.pixelWidthValue, (value) => value === 0 ? "Auto" : `${value}px`);
bindRange(elements.tolerance, elements.toleranceNumber, elements.toleranceValue);
bindRange(elements.binSize, elements.binSizeNumber, elements.binSizeValue);

async function checkApi() {
  try {
    const response = await fetch("/api/convert", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Conversion service unavailable");
    const details = await response.json();
    state.apiReady = details.status === "ready";
    elements.engineStatus.textContent = state.apiReady ? "Engine ready" : "Engine unavailable";
    elements.engineStatus.classList.toggle("ready", state.apiReady);
  } catch {
    state.apiReady = false;
    elements.engineStatus.textContent = "Engine unavailable";
    elements.engineStatus.classList.add("error");
  }
  updateConvertState();
}

function acceptFile(file) {
  clearError();
  if (!file) return;
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) {
    showError("Choose a PNG, JPG, or WebP image.");
    return;
  }
  if (file.size > 4_000_000) {
    showError("The image must be smaller than 4 MB for online conversion.");
    return;
  }
  state.file = file;
  elements.dropzone.classList.add("has-file");
  elements.fileName.textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
  updateConvertState();
  announce(`${file.name} selected and ready to convert.`);
}

elements.fileInput.addEventListener("change", () => acceptFile(elements.fileInput.files[0]));
["dragenter", "dragover"].forEach((eventName) => {
  elements.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropzone.classList.add("dragging");
  });
});
["dragleave", "drop"].forEach((eventName) => {
  elements.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropzone.classList.remove("dragging");
  });
});
elements.dropzone.addEventListener("drop", (event) => acceptFile(event.dataTransfer.files[0]));

function imageFromUrl(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Could not decode the converted image."));
    image.src = url;
  });
}

function renderPreview() {
  if (!state.mappedImage || !state.result) return;
  const zoom = Number(elements.zoom.value);
  const { width, height } = state.result.grid;
  const canvas = elements.resultCanvas;
  canvas.width = width * zoom;
  canvas.height = height * zoom;
  const context = canvas.getContext("2d");
  context.imageSmoothingEnabled = false;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(state.mappedImage, 0, 0, canvas.width, canvas.height);

  if (elements.showGrid.checked && zoom >= 7) {
    context.beginPath();
    context.strokeStyle = "rgba(5, 8, 5, .42)";
    context.lineWidth = 1;
    for (let x = zoom; x < canvas.width; x += zoom) {
      context.moveTo(x + .5, 0);
      context.lineTo(x + .5, canvas.height);
    }
    for (let y = zoom; y < canvas.height; y += zoom) {
      context.moveTo(0, y + .5);
      context.lineTo(canvas.width, y + .5);
    }
    context.stroke();
  }
  elements.zoomValue.textContent = `${zoom}×`;
}

function hideCellTooltip() {
  elements.cellTooltip.hidden = true;
}

function showCellTooltip(event) {
  if (!state.result || !state.cleanedPixelContext || elements.resultCanvas.hidden) {
    hideCellTooltip();
    return;
  }
  const canvas = elements.resultCanvas;
  const stage = document.getElementById("canvasStage");
  const rect = canvas.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX >= rect.right || event.clientY < rect.top || event.clientY >= rect.bottom) {
    hideCellTooltip();
    return;
  }
  const { width, height } = state.result.grid;
  const x = Math.min(width - 1, Math.max(0, Math.floor((event.clientX - rect.left) * width / rect.width)));
  const y = Math.min(height - 1, Math.max(0, Math.floor((event.clientY - rect.top) * height / rect.height)));
  const pixel = state.cleanedPixelContext.getImageData(x, y, 1, 1).data;
  const match = state.colorLookup.get(`${pixel[0]},${pixel[1]},${pixel[2]}`);
  elements.cellTooltip.replaceChildren();
  const title = document.createElement("strong");
  title.textContent = match ? match.material_name : (pixel[3] ? "Unmapped color" : "Background");
  elements.cellTooltip.append(title);
  if (match) {
    const detail = document.createElement("span");
    detail.textContent = `${match.material_type} · ${match.source_hex}`;
    elements.cellTooltip.append(detail);
  } else {
    const detail = document.createElement("span");
    detail.textContent = pixel[3] ? "No Terraria match" : "Removed or transparent";
    elements.cellTooltip.append(detail);
  }
  const stageRect = stage.getBoundingClientRect();
  const left = event.clientX - stageRect.left + stage.scrollLeft + 16;
  const top = event.clientY - stageRect.top + stage.scrollTop + 16;
  elements.cellTooltip.style.left = `${Math.min(Math.max(8, left), Math.max(8, stage.scrollWidth - 190))}px`;
  elements.cellTooltip.style.top = `${Math.min(Math.max(8, top), Math.max(8, stage.scrollHeight - 64))}px`;
  elements.cellTooltip.hidden = false;
}

function renderMaterials(filter = "") {
  const materials = state.result?.materials || [];
  const needle = filter.trim().toLocaleLowerCase();
  const visible = materials.filter((item) => `${item.material_name} ${item.material_type}`.toLocaleLowerCase().includes(needle));
  elements.materialRows.replaceChildren();

  if (!visible.length) {
    const row = document.createElement("tr");
    row.className = "placeholder-row";
    const cell = document.createElement("td");
    cell.colSpan = 3;
    cell.textContent = materials.length ? "No materials match that filter." : "No visible materials were found.";
    row.append(cell);
    elements.materialRows.append(row);
    return;
  }

  visible.forEach((item) => {
    const row = document.createElement("tr");
    const materialCell = document.createElement("td");
    const nameWrap = document.createElement("span");
    nameWrap.className = "material-name";
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    const [red, green, blue] = item.material_color;
    const hex = `#${[red, green, blue].map((channel) => channel.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
    swatch.style.backgroundColor = hex;
    swatch.setAttribute("aria-label", `Color ${hex}`);
    const label = document.createElement("span");
    label.textContent = item.material_name;
    const meta = document.createElement("small");
    meta.className = "material-meta";
    meta.textContent = hex;
    label.append(meta);
    nameWrap.append(swatch, label);
    materialCell.append(nameWrap);

    const typeCell = document.createElement("td");
    const typeTag = document.createElement("span");
    typeTag.className = "type-tag";
    typeTag.textContent = item.material_type;
    typeCell.append(typeTag);

    const countCell = document.createElement("td");
    countCell.className = "number";
    countCell.textContent = item.pixel_count.toLocaleString();
    row.append(materialCell, typeCell, countCell);
    elements.materialRows.append(row);
  });
}

async function convertImage() {
  if (!state.file || state.processing) return;
  clearError();
  state.processing = true;
  elements.loadingOverlay.hidden = false;
  updateConvertState();
  elements.convertButton.textContent = "Converting…";
  elements.engineStatus.textContent = "Working";
  document.body.setAttribute("aria-busy", "true");
  announce("Conversion started.");

  const params = new URLSearchParams({
    remove_background: elements.removeBackground.checked ? "1" : "0",
    tolerance: elements.toleranceNumber.value,
    pixel_width: elements.pixelWidthNumber.value,
    bin_size: elements.binSizeNumber.value,
    palette: elements.paletteMode.value,
  });

  try {
    const response = await fetch(`/api/convert?${params}`, {
      method: "POST",
      headers: { "Content-Type": state.file.type || "application/octet-stream", Accept: "application/json" },
      body: state.file,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(readableError(
        payload.error ?? payload.detail ?? payload.message,
        `The image could not be converted (${response.status}). Try Auto pixel width or a smaller custom value.`,
      ));
    }

    const [cleanedImage, mappedImage] = await Promise.all([
      imageFromUrl(payload.cleaned_png),
      imageFromUrl(payload.mapped_png),
    ]);
    state.result = payload;
    state.cleanedImage = cleanedImage;
    state.mappedImage = mappedImage;
    state.colorLookup = new Map((payload.color_matches || []).map((match) => [match.source_color.join(","), match]));
    const pixelCanvas = document.createElement("canvas");
    pixelCanvas.width = payload.grid.width;
    pixelCanvas.height = payload.grid.height;
    state.cleanedPixelContext = pixelCanvas.getContext("2d", { willReadFrequently: true });
    state.cleanedPixelContext.imageSmoothingEnabled = false;
    state.cleanedPixelContext.drawImage(cleanedImage, 0, 0, pixelCanvas.width, pixelCanvas.height);

    elements.realStat.textContent = `${payload.source.width} × ${payload.source.height}`;
    elements.gridStat.textContent = `${payload.grid.width} × ${payload.grid.height}`;
    elements.visibleStat.textContent = payload.visible_pixels.toLocaleString();
    elements.timeStat.textContent = `${payload.processing_ms} ms`;
    elements.materialTotal.textContent = `${payload.visible_pixels.toLocaleString()} tiles`;
    elements.emptyState.hidden = true;
    elements.resultCanvas.hidden = false;
    elements.zoom.disabled = false;
    elements.materialSearch.disabled = false;
    elements.downloadClean.disabled = false;
    elements.downloadMapped.disabled = false;
    elements.downloadCsv.disabled = false;
    elements.materialSearch.value = "";
    renderPreview();
    renderMaterials();
    announce(`Conversion complete. Grid ${payload.grid.width} by ${payload.grid.height}, ${payload.materials.length} material types.`);
  } catch (error) {
    showError(error.message || "Conversion failed. Please try again.");
  } finally {
    state.processing = false;
    elements.loadingOverlay.hidden = true;
    document.body.removeAttribute("aria-busy");
    elements.convertButton.textContent = "Convert image";
    elements.engineStatus.textContent = state.apiReady ? "Engine ready" : "Engine unavailable";
    updateConvertState();
  }
}

elements.convertButton.addEventListener("click", convertImage);
elements.zoom.addEventListener("input", renderPreview);
elements.showGrid.addEventListener("change", renderPreview);
elements.resultCanvas.addEventListener("pointermove", showCellTooltip);
elements.resultCanvas.addEventListener("pointerleave", hideCellTooltip);
elements.materialSearch.addEventListener("input", () => renderMaterials(elements.materialSearch.value));

function downloadUrl(url, filename) {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
}

elements.downloadClean.addEventListener("click", () => {
  downloadUrl(state.result.cleaned_png, `${state.file.name.replace(/\.[^.]+$/, "")}_clean_1x.png`);
});

elements.downloadMapped.addEventListener("click", () => {
  const canvas = document.createElement("canvas");
  canvas.width = state.result.source.width;
  canvas.height = state.result.source.height;
  const context = canvas.getContext("2d");
  context.imageSmoothingEnabled = false;
  context.drawImage(state.mappedImage, 0, 0, canvas.width, canvas.height);
  downloadUrl(canvas.toDataURL("image/png"), `${state.file.name.replace(/\.[^.]+$/, "")}_terraria.png`);
});

elements.downloadCsv.addEventListener("click", () => {
  const rows = [["material", "type", "count", "red", "green", "blue"]];
  state.result.materials.forEach((item) => rows.push([
    item.material_name,
    item.material_type,
    item.pixel_count,
    ...item.material_color,
  ]));
  const csv = rows.map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\n");
  downloadUrl(URL.createObjectURL(new Blob([csv], { type: "text/csv" })), `${state.file.name.replace(/\.[^.]+$/, "")}_materials.csv`);
});

elements.contrastToggle.addEventListener("click", () => {
  const enabled = document.body.classList.toggle("high-contrast");
  elements.contrastToggle.setAttribute("aria-pressed", String(enabled));
});

document.querySelectorAll("[data-dialog]").forEach((button) => {
  button.addEventListener("click", () => document.getElementById(button.dataset.dialog).showModal());
});
document.querySelectorAll("dialog").forEach((dialog) => {
  dialog.querySelector(".dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
});

function registerWebMcpTools() {
  const context = document.modelContext;
  if (!context?.registerTool) return;
  context.registerTool({
    name: "set_converter_options",
    title: "Set converter options",
    description: "Update the visible pixel-width, background, tolerance, and Terraria palette controls.",
    inputSchema: {
      type: "object",
      properties: {
        pixelWidth: { type: "integer", minimum: 0, maximum: 256 },
        removeBackground: { type: "boolean" },
        backgroundTolerance: { type: "integer", minimum: 0, maximum: 255 },
        palette: { type: "string", enum: ["both", "blocks", "walls"] },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, untrustedContentHint: false },
    execute(input) {
      if (Number.isInteger(input.pixelWidth)) {
        elements.pixelWidthNumber.value = input.pixelWidth;
        elements.pixelWidthNumber.dispatchEvent(new Event("input"));
      }
      if (typeof input.removeBackground === "boolean") elements.removeBackground.checked = input.removeBackground;
      if (Number.isInteger(input.backgroundTolerance)) {
        elements.toleranceNumber.value = input.backgroundTolerance;
        elements.toleranceNumber.dispatchEvent(new Event("input"));
      }
      if (["both", "blocks", "walls"].includes(input.palette)) elements.paletteMode.value = input.palette;
      return {
        pixelWidth: Number(elements.pixelWidthNumber.value),
        removeBackground: elements.removeBackground.checked,
        backgroundTolerance: Number(elements.toleranceNumber.value),
        palette: elements.paletteMode.value,
      };
    },
  });
}

registerWebMcpTools();
checkApi();
