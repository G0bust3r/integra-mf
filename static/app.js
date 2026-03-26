const state = {
  files: [],
  records: [],
  understood: [],
  overrides: [],
  selectedAccountId: "picpay_account",
  selectedCardId: "picpay_0037",
  reference: {
    accounts: [],
    cards: [],
    observations: [],
  },
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
  overridesList: document.getElementById("overridesList"),
  understoodList: document.getElementById("understoodList"),
  recordCount: document.getElementById("recordCount"),
  selectedCount: document.getElementById("selectedCount"),
  recordTemplate: document.getElementById("recordTemplate"),
  defaultObservation: document.getElementById("defaultObservation"),
  entryTypes: document.querySelectorAll('input[name="entryType"]'),
  themeToggle: document.getElementById("themeToggle"),
  walletPicker: document.getElementById("walletPicker"),
  selectionTitle: document.getElementById("selectionTitle"),
  selectionSubtitle: document.getElementById("selectionSubtitle"),
  selectedSummary: document.getElementById("selectedSummary"),
};

const THEME_KEY = "integra-mf-theme";
const ACCOUNT_OPTIONS = [
  { id: "picpay_account", name: "Pic Pay", type: "Conta Corrente", accent: "#4ade80", summary: "Conta principal", accountValue: "Pic Pay" },
  { id: "inter_account", name: "Banco Inter", type: "Conta Corrente", accent: "#fb923c", summary: "Conta Corrente", accountValue: "Banco Inter" },
  { id: "inter_pj_account", name: "Banco Inter PJ", type: "Conta Corrente", accent: "#fb923c", summary: "Conta PJ", accountValue: "Banco Inter PJ" },
  { id: "mercadopago_account", name: "MercadoPago", type: "Conta Corrente", accent: "#60a5fa", summary: "Conta Corrente", accountValue: "MercadoPago" },
  { id: "votorantim_account", name: "Banco Votorantim", type: "Conta Corrente", accent: "#93c5fd", summary: "Conta Corrente", accountValue: "Banco Votorantim" },
  { id: "next_account", name: "Next", type: "Conta Corrente", accent: "#22c55e", summary: "Conta Corrente", accountValue: "Next" },
  { id: "recargapay_account", name: "RecargaPay", type: "Conta Corrente", accent: "#f59e0b", summary: "Conta Corrente", accountValue: "RecargaPay" },
  { id: "corinthians_account", name: "Corinthians", type: "Conta Corrente", accent: "#f59e0b", summary: "Conta Corrente", accountValue: "Corinthians" },
  { id: "reserva_account", name: "Reserva de Emergência", type: "Investimentos", accent: "#94a3b8", summary: "Reserva", accountValue: "Reserva de Emergência" },
  { id: "investimentos_account", name: "Investimentos", type: "Investimentos", accent: "#94a3b8", summary: "Investimentos", accountValue: "Investimentos" },
  { id: "viagem_account", name: "Viagem", type: "Investimentos", accent: "#c084fc", summary: "Objetivo", accountValue: "Viagem" },
];
const CREDIT_CARD_OPTIONS = [
  { id: "picpay_0037", name: "Pic Pay", brand: "Pic Pay", last4: "0037", accountValue: "Pic Pay", accent: "#4ade80", closingDay: 5, dueDay: 13 },
  { id: "inter_7449", name: "Inter", brand: "Inter", last4: "7449", accountValue: "Pic Pay", accent: "#fb923c", closingDay: 30, dueDay: 9 },
  { id: "bv_3367", name: "Banco BV", brand: "Banco BV", last4: "3367", accountValue: "Pic Pay", accent: "#60a5fa", closingDay: 5, dueDay: 13 },
  { id: "next_3208", name: "Next", brand: "Next", last4: "3208", accountValue: "Next", accent: "#22c55e", closingDay: 27, dueDay: 10 },
  { id: "recargapay_2099", name: "RecargaPay", brand: "RecargaPay", last4: "2099", accountValue: "RecargaPay", accent: "#f59e0b", closingDay: 1, dueDay: 1 },
  { id: "bmg_3388", name: "BMG Multi", brand: "BMG Multi", last4: "3388", accountValue: "Corinthians", accent: "#f59e0b", closingDay: 6, dueDay: 15 },
];

function getBrandMark(item, entryType) {
  const key = normalizeText(item.brand || item.name);
  if (entryType === "credit_card") {
    return `
      <span class="network-mark">
        <span class="network-dot left"></span>
        <span class="network-dot right"></span>
      </span>
    `;
  }

  const tokenMap = {
    "pic pay": "P",
    "banco inter": "IN",
    "banco inter pj": "IP",
    "mercadopago": "MP",
    "banco votorantim": "BV",
    "next": "NX",
    "recargapay": "RP",
    "corinthians": "CO",
    "reserva de emergencia": "RE",
    "investimentos": "IV",
    "viagem": "VG",
  };
  return `<span class="logo-token">${escapeHtml(tokenMap[key] || (item.name || "").slice(0, 2).toUpperCase())}</span>`;
}

function getWalletIcon(item, entryType) {
  return `
    <div class="wallet-icon-frame">
      <div class="wallet-icon-brand">${getBrandMark(item, entryType)}</div>
    </div>
  `;
}

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

function parseDateParts(value) {
  const [dateToken] = String(value).trim().split(" ");
  const [day, month, year] = dateToken.split("/").map(Number);
  return new Date(year, month - 1, day);
}

function formatDate(date) {
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const year = date.getFullYear();
  return `${day}/${month}/${year}`;
}

function getTodayDate() {
  return formatDate(new Date());
}

function adjustBusinessDay(date) {
  const adjusted = new Date(date.getTime());
  while (adjusted.getDay() === 0 || adjusted.getDay() === 6) {
    adjusted.setDate(adjusted.getDate() + 1);
  }
  return adjusted;
}

function computeCardDueDate(card, launchDate) {
  const reference = launchDate ? parseDateParts(launchDate) : parseDateParts(getTodayDate());
  let closing = new Date(reference.getFullYear(), reference.getMonth(), card.closingDay);
  if (reference > closing) {
    closing = new Date(reference.getFullYear(), reference.getMonth() + 1, card.closingDay);
  }
  const dueMonthOffset = card.dueDay < card.closingDay ? 1 : 0;
  let due = new Date(closing.getFullYear(), closing.getMonth() + dueMonthOffset, card.dueDay);
  due = adjustBusinessDay(due);
  return formatDate(due);
}

function getSelectedAccount() {
  return ACCOUNT_OPTIONS.find((item) => item.id === state.selectedAccountId) || ACCOUNT_OPTIONS[0];
}

function getSelectedCard() {
  return CREDIT_CARD_OPTIONS.find((item) => item.id === state.selectedCardId) || CREDIT_CARD_OPTIONS[0];
}

function getWalletChoices() {
  return getSelectedEntryType() === "credit_card" ? CREDIT_CARD_OPTIONS : ACCOUNT_OPTIONS;
}

function renderWalletPicker() {
  const entryType = getSelectedEntryType();
  const items = getWalletChoices();
  els.selectionTitle.textContent = entryType === "credit_card" ? "Selecione o cartão cadastrado" : "Selecione a conta cadastrada";
  els.selectionSubtitle.textContent = entryType === "credit_card"
    ? "O app usa a conta vinculada, mantém a data da despesa como lançamento e calcula o vencimento da fatura em dia útil."
    : "Escolha visualmente a conta corrente ou carteira de destino para as despesas normais.";
  els.selectedSummary.textContent = entryType === "credit_card"
    ? `${getSelectedCard().brand} • final ${getSelectedCard().last4} • conta ${getSelectedCard().accountValue}`
    : `${getSelectedAccount().name} • ${getSelectedAccount().type}`;

  els.walletPicker.innerHTML = items
    .map((item) => {
      const selected = entryType === "credit_card" ? item.id === state.selectedCardId : item.id === state.selectedAccountId;
      const meta = entryType === "credit_card"
        ? `
          <div><strong>Conta</strong><span>${escapeHtml(item.accountValue)}</span></div>
          <div><strong>Final</strong><span>${escapeHtml(item.last4)}</span></div>
          <div><strong>Fechamento</strong><span>${String(item.closingDay).padStart(2, "0")}</span></div>
          <div><strong>Vencimento</strong><span>${String(item.dueDay).padStart(2, "0")}</span></div>
        `
        : `
          <div><strong>Tipo</strong><span>${escapeHtml(item.type)}</span></div>
          <div><strong>Conta</strong><span>${escapeHtml(item.accountValue)}</span></div>
          <div><strong>Visual</strong><span>${escapeHtml(item.summary)}</span></div>
          <div><strong>Status</strong><span>Disponível</span></div>
        `;
      return `
        <button
          type="button"
          class="wallet-card ${selected ? "selected" : ""}"
          data-wallet-id="${escapeHtml(item.id)}"
          style="--wallet-color:${escapeHtml(item.accent)}"
        >
          <div class="wallet-card-header">
            ${getWalletIcon(item, entryType)}
            <div class="wallet-brand">
              <strong>${escapeHtml(item.name)}</strong>
              <span>${entryType === "credit_card" ? `${escapeHtml(item.brand)} • ${escapeHtml(item.last4)}` : escapeHtml(item.type)}</span>
            </div>
            <div class="wallet-chip">${entryType === "credit_card" ? "CARD" : "ACC"}</div>
          </div>
          <div class="wallet-number">
            ${entryType === "credit_card" ? `Cartão final ${escapeHtml(item.last4)}` : escapeHtml(item.accountValue)}
          </div>
          <div class="wallet-subnumber">
            ${entryType === "credit_card" ? `•••• •••• •••• ${escapeHtml(item.last4)}` : `Conta ativa • ${escapeHtml(item.summary)}`}
          </div>
          <div class="wallet-meta">${meta}</div>
          <div class="wallet-footer">
            <span>${entryType === "credit_card" ? "Integra MF" : "Conta selecionável"}</span>
            <span>${entryType === "credit_card" ? "Ajuste automático" : escapeHtml(item.summary)}</span>
          </div>
        </button>
      `;
    })
    .join("");

  els.walletPicker.querySelectorAll("[data-wallet-id]").forEach((button) => {
    button.addEventListener("click", () => {
      if (entryType === "credit_card") {
        state.selectedCardId = button.dataset.walletId;
      } else {
        state.selectedAccountId = button.dataset.walletId;
      }
      renderWalletPicker();
      applyDefaults();
    });
  });
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

function levenshtein(a, b) {
  const left = normalizeText(a);
  const right = normalizeText(b);
  const matrix = Array.from({ length: left.length + 1 }, () => Array(right.length + 1).fill(0));
  for (let i = 0; i <= left.length; i += 1) matrix[i][0] = i;
  for (let j = 0; j <= right.length; j += 1) matrix[0][j] = j;
  for (let i = 1; i <= left.length; i += 1) {
    for (let j = 1; j <= right.length; j += 1) {
      const cost = left[i - 1] === right[j - 1] ? 0 : 1;
      matrix[i][j] = Math.min(
        matrix[i - 1][j] + 1,
        matrix[i][j - 1] + 1,
        matrix[i - 1][j - 1] + cost,
      );
    }
  }
  return matrix[left.length][right.length];
}

function fuzzyMatchValue(value, candidates) {
  const input = value.trim();
  if (!input || !candidates.length) return input;
  const normalizedInput = normalizeText(input);
  let best = input;
  let bestScore = 0;

  candidates.forEach((candidate) => {
    const normalizedCandidate = normalizeText(candidate);
    if (!normalizedCandidate) return;
    if (normalizedCandidate === normalizedInput) {
      best = candidate;
      bestScore = 1;
      return;
    }
    if (normalizedCandidate.includes(normalizedInput) || normalizedInput.includes(normalizedCandidate)) {
      const inclusionScore = Math.min(normalizedInput.length, normalizedCandidate.length) / Math.max(normalizedInput.length, normalizedCandidate.length);
      if (inclusionScore > bestScore) {
        best = candidate;
        bestScore = inclusionScore;
      }
      return;
    }
    const distance = levenshtein(normalizedInput, normalizedCandidate);
    const similarity = 1 - distance / Math.max(normalizedInput.length, normalizedCandidate.length, 1);
    if (similarity > bestScore) {
      best = candidate;
      bestScore = similarity;
    }
  });

  return bestScore >= 0.56 ? best : input;
}

function normalizeDefaultInputs() {
  const matchedObservation = fuzzyMatchValue(els.defaultObservation.value, state.reference.observations);
  if (matchedObservation) els.defaultObservation.value = matchedObservation;
  return {
    account: getSelectedAccount().accountValue,
    card: getSelectedCard().last4,
    observation: matchedObservation.trim(),
  };
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

function renderOverrides() {
  if (!els.overridesList) return;
  if (!state.overrides.length) {
    els.overridesList.innerHTML = '<div class="empty-box">Nenhum override salvo ainda.</div>';
    return;
  }

  els.overridesList.innerHTML = state.overrides
    .map((override) => `
      <article class="override-item">
        <div class="override-item-top">
          <strong>${escapeHtml(override.description || "Sem descrição")}</strong>
          <span>${escapeHtml(override.entry_type === "credit_card" ? "Cartão" : "Conta")}</span>
        </div>
        <p>${escapeHtml(override.match_text || "")}</p>
        <div class="override-tags">
          ${override.account ? `<span>${escapeHtml(override.account)}</span>` : ""}
          ${override.card ? `<span>Cartão ${escapeHtml(override.card)}</span>` : ""}
          ${override.category ? `<span>${escapeHtml(override.category)}</span>` : ""}
          ${override.subcategory ? `<span>${escapeHtml(override.subcategory)}</span>` : ""}
          ${override.observations ? `<span>${escapeHtml(override.observations)}</span>` : ""}
        </div>
      </article>
    `).join("");
}

function getSelectedEntryType() {
  const selected = Array.from(els.entryTypes).find((input) => input.checked);
  return selected ? selected.value : "account";
}

function applyDefaults() {
  const entryType = getSelectedEntryType();
  const normalizedDefaults = normalizeDefaultInputs();
  const observation = normalizedDefaults.observation;

  state.records = state.records.map((record) => {
    const next = { ...record };
    next.entry_type = next.entry_type || entryType;
    if (!next.observations && observation) next.observations = observation;

    if (next.entry_type === "credit_card") {
      const selectedCard = getSelectedCard();
      const referenceDate = next.launch_date || next.due_date || getTodayDate();
      next.account = selectedCard.accountValue;
      next.card = selectedCard.last4;
      if (!next.launch_date) next.launch_date = referenceDate;
      next.due_date = computeCardDueDate(selectedCard, next.launch_date || referenceDate);
    } else {
      next.account = getSelectedAccount().accountValue;
      next.card = "";
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
    if (field === "entry_type") {
      if (value === "credit_card") {
        const selectedCard = getSelectedCard();
        const referenceDate = state.records[index].launch_date || state.records[index].due_date || getTodayDate();
        state.records[index].account = selectedCard.accountValue;
        state.records[index].card = selectedCard.last4;
        state.records[index].launch_date = state.records[index].launch_date || referenceDate;
        state.records[index].due_date = computeCardDueDate(selectedCard, state.records[index].launch_date || referenceDate);
      } else {
        state.records[index].account = getSelectedAccount().accountValue;
        state.records[index].card = "";
      }
    }
    if (field === "launch_date" && state.records[index].entry_type === "credit_card") {
      state.records[index].due_date = computeCardDueDate(getSelectedCard(), value || getTodayDate());
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
    const duplicateBadge = clone.querySelector(".duplicate-badge");
    const duplicateNote = clone.querySelector(".duplicate-note");
    const learnedBadge = clone.querySelector(".learned-badge");
    const learnedNote = clone.querySelector(".learned-note");
    const saveOverrideBtn = clone.querySelector(".save-override-btn");
    if (record.duplicate && record.duplicate_match) {
      duplicateBadge.hidden = false;
      duplicateNote.hidden = false;
      duplicateNote.textContent = `Já existe algo muito parecido: ${record.duplicate_match.description} · ${record.duplicate_match.amount} · ${record.duplicate_match.due_date} · ${record.duplicate_match.source_file}`;
    }
    if (record.applied_override) {
      learnedBadge.hidden = false;
      learnedNote.hidden = false;
      learnedNote.textContent = `Aprendido antes com similaridade ${record.applied_override.score}: ${record.applied_override.match_text}`;
    }
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
    saveOverrideBtn.addEventListener("click", async () => {
      await saveOverride(index);
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
  normalizeDefaultInputs();
  const selectedAccount = getSelectedAccount();
  const selectedCard = getSelectedCard();
  state.records = (payload.records || []).map((record) => {
    const launchDate = record.launch_date || record.due_date || getTodayDate();
    const next = {
      ...record,
      selected: record.duplicate ? false : true,
      entry_type: entryType,
      amount: record.amount ? String(Math.abs(Number(record.amount) || 0).toFixed(2)) : "",
      launch_date: entryType === "credit_card" ? launchDate : (record.launch_date || ""),
      due_date: entryType === "credit_card" ? computeCardDueDate(selectedCard, launchDate) : (record.due_date || ""),
      account: entryType === "credit_card" ? selectedCard.accountValue : (record.account || selectedAccount.accountValue),
      card: entryType === "credit_card" ? selectedCard.last4 : "",
    };
    return next;
  });
  state.understood = state.records.map((record) => ({
    source: record.source_file,
    text: record.raw_text || "",
  }));
  applyDefaults();
  renderUnderstood();
  renderDiagnostics(payload.diagnostics || [`${payload.count || 0} linhas reconhecidas.`]);
}

async function loadOverrides() {
  try {
    const response = await fetch("/api/overrides");
    if (!response.ok) return;
    const payload = await response.json();
    state.overrides = payload.overrides || [];
    renderOverrides();
  } catch (_error) {
    state.overrides = [];
    renderOverrides();
  }
}

async function loadReferenceData() {
  try {
    const response = await fetch("/api/reference");
    if (!response.ok) return;
    const payload = await response.json();
    state.reference = {
      accounts: payload.accounts || [],
      cards: payload.cards || [],
      observations: payload.observations || [],
    };
  } catch (_error) {
    state.reference = { accounts: [], cards: [], observations: [] };
  }
}

async function saveOverride(index) {
  const record = state.records[index];
  if (!record) return;
  renderDiagnostics(["Salvando override de aprendizado..."]);
  const response = await fetch("/api/overrides", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ record }),
  });
  const payload = await response.json();
  if (!response.ok) {
    renderDiagnostics([payload.error || "Falha ao salvar override."]);
    return;
  }
  state.overrides = payload.overrides || [];
  loadReferenceData();
  state.records[index].applied_override = {
    id: payload.override?.id || "",
    match_text: payload.override?.match_text || record.raw_text || record.description,
    score: 1,
  };
  renderOverrides();
  renderEditors();
  renderDiagnostics(["Override salvo. A partir de agora esse ajuste será reaplicado nas próximas importações."]);
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
els.entryTypes.forEach((input) => input.addEventListener("change", () => {
  renderWalletPicker();
  applyDefaults();
}));
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
loadReferenceData();
loadOverrides();
renderWalletPicker();
renderSelectedFiles();
renderEditors();
renderUnderstood();
renderOverrides();
