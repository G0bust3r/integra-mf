const state = {
  files: [],
  records: [],
  understood: [],
};

const els = {
  filesInput: document.getElementById("files"),
  selectedFiles: document.getElementById("selectedFiles"),
  diagnostics: document.getElementById("diagnostics"),
  processBtn: document.getElementById("processBtn"),
  applyDefaultsBtn: document.getElementById("applyDefaultsBtn"),
  clearBtn: document.getElementById("clearBtn"),
  exportBtn: document.getElementById("exportBtn"),
  recordsEditor: document.getElementById("recordsEditor"),
  understoodList: document.getElementById("understoodList"),
  recordCount: document.getElementById("recordCount"),
  selectedCount: document.getElementById("selectedCount"),
  recordTemplate: document.getElementById("recordTemplate"),
  defaultAccount: document.getElementById("defaultAccount"),
  defaultCard: document.getElementById("defaultCard"),
  defaultDueDate: document.getElementById("defaultDueDate"),
  defaultObservation: document.getElementById("defaultObservation"),
  entryTypes: document.querySelectorAll('input[name="entryType"]'),
  themeToggle: document.getElementById("themeToggle"),
};

const THEME_KEY = "integra-mf-theme";

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function normalizeText(value) {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  if (els.themeToggle) {
    els.themeToggle.textContent = theme === "dark" ? "Modo claro" : "Modo escuro";
  }
  window.localStorage.setItem(THEME_KEY, theme);
}

function initTheme() {
  const savedTheme = window.localStorage.getItem(THEME_KEY);
  setTheme(savedTheme || "dark");
}

function renderSelectedFiles() {
  els.selectedFiles.innerHTML = state.files.length
    ? state.files.map((file) => `<li>${escapeHtml(file.name)} <small>(${Math.round(file.size / 1024) || 1} KB)</small></li>`).join("")
    : "<li>Nenhum arquivo selecionado.</li>";
}

function renderDiagnostics(messages = []) {
  els.diagnostics.innerHTML = messages.length
    ? messages.map((message) => `<p>${escapeHtml(message)}</p>`).join("")
    : "";
}

function getSelectedEntryType() {
  const selected = Array.from(els.entryTypes).find((input) => input.checked);
  return selected ? selected.value : "account";
}

function applyDefaults() {
  const entryType = getSelectedEntryType();
  const account = els.defaultAccount.value.trim();
  const card = els.defaultCard.value.trim();
  const dueDate = els.defaultDueDate.value.trim();
  const observation = els.defaultObservation.value.trim();

  state.records = state.records.map((record) => {
    const next = { ...record };
    next.entry_type = next.entry_type || entryType;
    if (!next.account && account) next.account = account;
    if (!next.card && card) next.card = card;
    if (!next.due_date && dueDate) next.due_date = dueDate;
    if (!next.observations && observation) next.observations = observation;
    if (next.entry_type !== "credit_card") {
      next.card = "";
    } else if (!next.card && card) {
      next.card = card;
    }
    return next;
  });

  renderEditors();
}

function updateCounters() {
  els.recordCount.textContent = String(state.records.length);
  els.selectedCount.textContent = String(state.records.filter((record) => record.selected !== false).length);
}

function onFieldChange(index, field, value) {
  if (field === "selected") {
    state.records[index][field] = Boolean(value);
  } else {
    state.records[index][field] = value;
    if (field === "entry_type" && value !== "credit_card") {
      state.records[index].card = "";
    }
  }
  renderEditors();
}

function renderUnderstood() {
  if (!state.understood.length) {
    els.understoodList.innerHTML = '<div class="empty-box">Nada processado ainda.</div>';
    return;
  }
  els.understoodList.innerHTML = state.understood
    .map(
      (item) => `
        <article class="understood-item">
          <strong>${escapeHtml(item.source)}</strong>
          <pre>${escapeHtml(item.text || "Sem texto bruto disponível.")}</pre>
        </article>
      `,
    )
    .join("");
}

function renderEditors() {
  if (!state.records.length) {
    els.recordsEditor.innerHTML = '<div class="empty-box">Nenhum lançamento carregado ainda.</div>';
    updateCounters();
    return;
  }

  const fragment = document.createDocumentFragment();
  state.records.forEach((record, index) => {
    const clone = els.recordTemplate.content.firstElementChild.cloneNode(true);
    clone.querySelector(".source-chip").textContent = `${record.source_file} · confiança ${(record.confidence || 0).toFixed(2)}`;
    clone.querySelectorAll("[data-field]").forEach((input) => {
      const field = input.dataset.field;
      if (field === "selected") {
        input.checked = record.selected !== false;
        input.addEventListener("change", (event) => onFieldChange(index, field, event.target.checked));
      } else if (input.tagName === "SELECT") {
        input.value = record[field] || "account";
        input.addEventListener("change", (event) => onFieldChange(index, field, event.target.value));
      } else {
        input.value = record[field] || "";
        input.addEventListener("input", (event) => onFieldChange(index, field, event.target.value));
      }
    });
    fragment.appendChild(clone);
  });

  els.recordsEditor.innerHTML = "";
  els.recordsEditor.appendChild(fragment);
  updateCounters();
}

async function processFiles() {
  if (!state.files.length) {
    renderDiagnostics(["Selecione ao menos um arquivo antes de processar."]);
    return;
  }

  const formData = new FormData();
  state.files.forEach((file) => formData.append("files", file));
  renderDiagnostics(["Processando arquivos..."]);

  const response = await fetch("/api/process", {
    method: "POST",
    body: formData,
  });
  const payload = await response.json();
  if (!response.ok) {
    renderDiagnostics([payload.error || "Falha ao processar os arquivos."]);
    return;
  }

  const entryType = getSelectedEntryType();
  state.records = (payload.records || []).map((record) => ({
    ...record,
    selected: true,
    entry_type: entryType,
    amount: record.amount ? String(Math.abs(Number(record.amount) || 0).toFixed(2)) : "",
    due_date: record.due_date || els.defaultDueDate.value.trim(),
    account: record.account || els.defaultAccount.value.trim(),
    card: entryType === "credit_card" ? record.card || els.defaultCard.value.trim() : "",
  }));
  state.understood = state.records.map((record) => ({
    source: record.source_file,
    text: record.raw_text || "",
  }));
  applyDefaults();
  renderUnderstood();
  renderDiagnostics(payload.diagnostics || [`${payload.count || 0} linhas reconhecidas.`]);
}

async function exportCsv() {
  const records = state.records.filter((record) => record.selected !== false);
  if (!records.length) {
    renderDiagnostics(["Marque ao menos uma linha para exportar."]);
    return;
  }
  const response = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ records }),
  });
  if (!response.ok) {
    renderDiagnostics(["Falha ao gerar o CSV."]);
    return;
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "minhasfinancas.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}

els.filesInput.addEventListener("change", (event) => {
  state.files = Array.from(event.target.files || []);
  renderSelectedFiles();
});
els.processBtn.addEventListener("click", processFiles);
els.applyDefaultsBtn.addEventListener("click", applyDefaults);
els.entryTypes.forEach((input) => input.addEventListener("change", applyDefaults));
els.clearBtn.addEventListener("click", () => {
  state.files = [];
  state.records = [];
  state.understood = [];
  els.filesInput.value = "";
  renderSelectedFiles();
  renderEditors();
  renderUnderstood();
  renderDiagnostics([]);
});
els.exportBtn.addEventListener("click", exportCsv);
if (els.themeToggle) {
  els.themeToggle.addEventListener("click", () => {
    const currentTheme = document.documentElement.getAttribute("data-theme") || "dark";
    setTheme(currentTheme === "dark" ? "light" : "dark");
  });
}

initTheme();
renderSelectedFiles();
renderEditors();
renderUnderstood();
