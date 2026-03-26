const state = {
  files: [],
  records: [],
};

const els = {
  filesInput: document.getElementById("files"),
  selectedFiles: document.getElementById("selectedFiles"),
  diagnostics: document.getElementById("diagnostics"),
  processBtn: document.getElementById("processBtn"),
  sampleBtn: document.getElementById("sampleBtn"),
  applyDefaultsBtn: document.getElementById("applyDefaultsBtn"),
  clearBtn: document.getElementById("clearBtn"),
  exportBtn: document.getElementById("exportBtn"),
  recordsBody: document.getElementById("recordsBody"),
  recordCount: document.getElementById("recordCount"),
  selectedCount: document.getElementById("selectedCount"),
  rowTemplate: document.getElementById("rowTemplate"),
  defaultAccount: document.getElementById("defaultAccount"),
  defaultCard: document.getElementById("defaultCard"),
  defaultDueDate: document.getElementById("defaultDueDate"),
  defaultObservation: document.getElementById("defaultObservation"),
  rulesText: document.getElementById("rulesText"),
};

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

function getRules() {
  return els.rulesText.value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [keywordPart, mappingPart] = line.split("=");
      const [category = "", subcategory = ""] = (mappingPart || "").split(">");
      return {
        keyword: normalizeText(keywordPart || ""),
        category: category.trim(),
        subcategory: subcategory.trim(),
      };
    })
    .filter((rule) => rule.keyword && rule.category);
}

function applyDefaults() {
  const rules = getRules();
  const account = els.defaultAccount.value.trim();
  const card = els.defaultCard.value.trim();
  const dueDate = els.defaultDueDate.value.trim();
  const observation = els.defaultObservation.value.trim();

  state.records = state.records.map((record) => {
    const next = { ...record };
    if (!next.account && account) next.account = account;
    if (!next.card && card) next.card = card;
    if (!next.due_date && dueDate) next.due_date = dueDate;
    if (!next.observations && observation) next.observations = observation;

    const normalizedDescription = normalizeText(next.description || "");
    const matchedRule = rules.find((rule) => normalizedDescription.includes(rule.keyword));
    if (matchedRule) {
      if (!next.category) next.category = matchedRule.category;
      if (!next.subcategory) next.subcategory = matchedRule.subcategory;
    }
    return next;
  });

  renderRows();
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
  }
  updateCounters();
}

function renderRows() {
  if (!state.records.length) {
    els.recordsBody.innerHTML = '<tr class="empty-state"><td colspan="11">Nenhum lançamento carregado ainda.</td></tr>';
    updateCounters();
    return;
  }

  const fragment = document.createDocumentFragment();
  state.records.forEach((record, index) => {
    const clone = els.rowTemplate.content.firstElementChild.cloneNode(true);
    clone.querySelector(".source-cell").textContent = `${record.source_file} · ${(record.confidence || 0).toFixed(2)}`;
    clone.querySelectorAll("[data-field]").forEach((input) => {
      const field = input.dataset.field;
      if (field === "selected") {
        input.checked = record.selected !== false;
        input.addEventListener("change", (event) => onFieldChange(index, field, event.target.checked));
      } else {
        input.value = record[field] || "";
        input.addEventListener("input", (event) => onFieldChange(index, field, event.target.value));
      }
    });
    fragment.appendChild(clone);
  });

  els.recordsBody.innerHTML = "";
  els.recordsBody.appendChild(fragment);
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

  state.records = (payload.records || []).map((record) => ({ ...record, selected: true }));
  applyDefaults();
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

async function loadSample() {
  const sampleUrl = "file:///Users/lmv/Downloads/exemplo.xlsx";
  renderDiagnostics([
    "Para testar com o exemplo.xlsx, selecione o arquivo manualmente no campo acima. Navegadores bloqueiam leitura direta de file:// por seguranca.",
  ]);
  console.debug(sampleUrl);
}

els.filesInput.addEventListener("change", (event) => {
  state.files = Array.from(event.target.files || []);
  renderSelectedFiles();
});
els.processBtn.addEventListener("click", processFiles);
els.sampleBtn.addEventListener("click", loadSample);
els.applyDefaultsBtn.addEventListener("click", applyDefaults);
els.clearBtn.addEventListener("click", () => {
  state.files = [];
  state.records = [];
  els.filesInput.value = "";
  renderSelectedFiles();
  renderRows();
  renderDiagnostics([]);
});
els.exportBtn.addEventListener("click", exportCsv);

renderSelectedFiles();
renderRows();
