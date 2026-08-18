(() => {
  "use strict";

  const config = window.DOUGH_LOG || {};
  const form = document.querySelector("#recipe-form");
  if (!form) return;

  let flours = structuredClone(config.formula.flours || []);
  let ingredients = structuredClone(config.formula.ingredients || []);
  let preferments = structuredClone(config.formula.preferments || []);
  let mixStages = structuredClone(config.mixStages || []);
  const flourLibrary = structuredClone(config.flourLibrary || []);

  const number = (value, fallback = 0) => {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const optionalNumber = (value) => value === "" || value === null || value === undefined ? null : number(value);
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const inputValue = (name) => form.elements[name]?.value ?? "";
  const flourOptions = (currentName) => {
    const currentExists = flourLibrary.some((flour) => flour.name === currentName);
    const savedFormulaOption = currentName && !currentExists
      ? `<option value="${escapeHtml(currentName)}" selected>${escapeHtml(currentName)} · saved formula</option>`
      : "";
    const libraryOptions = flourLibrary.map((flour) => `
      <option value="${escapeHtml(flour.name)}" ${flour.name === currentName ? "selected" : ""}>${escapeHtml(flour.name)}</option>`).join("");
    return `<option value="">Choose saved flour…</option>${savedFormulaOption}${libraryOptions}`;
  };
  const flourRow = (item, attributes = "") => `
    <div class="repeat-row flour-row" ${attributes}>
      <label class="mini-label wide-input">Flour<select data-field="name">${flourOptions(item.name)}</select></label>
      <label class="mini-label">Blend %<input data-field="pct" type="number" min="0" step="0.01" value="${number(item.pct)}"></label>
      <label class="mini-label">Protein %<input data-field="protein_pct" type="number" min="0" step="0.01" value="${item.protein_pct ?? ""}" placeholder="required"></label>
      <label class="mini-label">Ash %<input data-field="ash_pct" type="number" min="0" step="0.01" value="${item.ash_pct ?? ""}" placeholder="required"></label>
      <button class="remove-row" type="button" data-remove-flour-row aria-label="Remove ${escapeHtml(item.name)}">×</button>
    </div>`;

  function renderFlours() {
    const target = document.querySelector("#flour-list");
    target.innerHTML = flours.map((item, index) => flourRow(item, `data-index="${index}" data-final-flour`)).join("");
  }

  function renderIngredients() {
    const target = document.querySelector("#ingredient-list");
    if (!ingredients.length) {
      target.innerHTML = '<div class="empty-state compact">No additional ingredients.</div>';
      return;
    }
    target.innerHTML = ingredients.map((item, index) => `
      <div class="repeat-row ingredient-row" data-index="${index}">
        <label class="mini-label wide-input">Ingredient<input data-field="name" value="${escapeHtml(item.name)}" placeholder="Canola Oil"></label>
        <label class="mini-label">Baker's %<input data-field="pct" type="number" min="0" step="0.01" value="${number(item.pct)}"></label>
        <button class="remove-row" type="button" data-remove-ingredient="${index}" aria-label="Remove ${escapeHtml(item.name)}">×</button>
      </div>`).join("");
  }

  function renderPreferments() {
    const target = document.querySelector("#preferment-list");
    if (!preferments.length) {
      target.innerHTML = '<div class="empty-state compact"><p>No preferments. Add a poolish or another independent build.</p></div>';
      return;
    }
    target.innerHTML = preferments.map((item, index) => {
      const componentTotal = number(item.water_pct) + number(item.leavening_pct);
      const flourPercent = Math.max(0, 100 - componentTotal);
      const prefFlours = item.flours?.length ? item.flours : [{ name: "Poolish Flour", pct: 100, protein_pct: null, ash_pct: null }];
      return `
      <article class="preferment-editor" data-index="${index}">
        <div class="preferment-editor-head"><div><p class="eyebrow">Build ${index + 1}</p><h3>${escapeHtml(item.name || "New Preferment")}</h3></div><button class="remove-row" type="button" data-remove-preferment="${index}" aria-label="Remove preferment">×</button></div>
        <div class="preferment-editor-grid">
          <label class="mini-label">Name<input data-field="name" value="${escapeHtml(item.name || "")}" placeholder="Poolish"></label>
          <label class="mini-label">Type<select data-field="type">${["Poolish", "Levain", "Biga", "Sponge", "Pâte fermentée", "Preferment"].map((type) => `<option ${item.type === type ? "selected" : ""}>${type}</option>`).join("")}</select></label>
          <label class="mini-label">Preferment % of total flour<input data-field="amount_pct" type="number" min="0" step="0.01" value="${number(item.amount_pct)}"></label>
          <label class="mini-label">Water % of preferment<input data-field="water_pct" type="number" min="0" max="100" step="0.01" value="${number(item.water_pct, 50)}"></label>
          <label class="mini-label">Leavening type<select data-field="leavening_type">${["None", "IDY", "ADY", "Cake Yeast", "Mature Starter"].map((type) => `<option ${item.leavening_type === type ? "selected" : ""}>${type}</option>`).join("")}</select></label>
          <label class="mini-label">Leavening % of preferment<input data-field="leavening_pct" type="number" min="0" max="100" step="0.001" value="${number(item.leavening_pct)}"></label>
          <div class="preferment-balance wide"><span>Flour balance</span><strong data-flour-balance>${flourPercent.toFixed(3)}%</strong><small>100% minus water and leavening</small></div>
          <label class="mini-label wide">Build notes<textarea data-field="notes" rows="2" placeholder="Temperature, maturity target, schedule…">${escapeHtml(item.notes || "")}</textarea></label>
        </div>
        <div class="nested-flour-block">
          <div class="repeat-heading"><h4>${escapeHtml(item.name || "Preferment")} Flour Blend</h4><button class="button small" type="button" data-add-pref-flour="${index}">Add Flour</button></div>
          <div class="repeat-list" data-pref-flours>${prefFlours.map((flour, flourIndex) => flourRow(flour, `data-flour-index="${flourIndex}" data-pref-flour`)).join("")}</div>
        </div>
      </article>`;
    }).join("");
  }

  function renderMixStages() {
    const target = document.querySelector("#mix-stage-list");
    if (!target) return;
    if (!mixStages.length) {
      target.innerHTML = '<div class="empty-state compact">No mixing stages recorded.</div>';
      updateMixTime();
      return;
    }
    target.innerHTML = mixStages.map((stage, index) => `
      <div class="repeat-row stage-row" data-index="${index}">
        <label class="mini-label">Speed / method<input data-field="speed" value="${escapeHtml(stage.speed || "")}" placeholder="Low"></label>
        <label class="mini-label">Minutes<input data-field="minutes" type="number" min="0" step="0.1" value="${number(stage.minutes)}"></label>
        <label class="mini-label stage-notes">Stage notes<input data-field="notes" value="${escapeHtml(stage.notes || "")}" placeholder="Add salt; bassinage; rest…"></label>
        <button class="remove-row" type="button" data-remove-stage="${index}" aria-label="Remove mixing stage">×</button>
      </div>`).join("");
    updateMixTime();
  }

  function readFlourRows(selector) {
    return [...document.querySelectorAll(selector)].map((row) => ({
      name: row.querySelector('[data-field="name"]').value.trim(),
      pct: number(row.querySelector('[data-field="pct"]').value),
      protein_pct: optionalNumber(row.querySelector('[data-field="protein_pct"]').value),
      ash_pct: optionalNumber(row.querySelector('[data-field="ash_pct"]').value)
    })).filter((item) => item.name);
  }

  function readRepeaters() {
    flours = readFlourRows("#flour-list [data-final-flour]");
    ingredients = [...document.querySelectorAll("#ingredient-list .ingredient-row")].map((row) => ({
      name: row.querySelector('[data-field="name"]').value.trim(),
      pct: number(row.querySelector('[data-field="pct"]').value)
    })).filter((item) => item.name);
    preferments = [...document.querySelectorAll("#preferment-list .preferment-editor")].map((row) => ({
      name: row.querySelector(':scope > .preferment-editor-grid [data-field="name"]').value.trim(),
      type: row.querySelector(':scope > .preferment-editor-grid [data-field="type"]').value,
      amount_pct: number(row.querySelector('[data-field="amount_pct"]').value),
      water_pct: number(row.querySelector('[data-field="water_pct"]').value),
      leavening_type: row.querySelector('[data-field="leavening_type"]').value,
      leavening_pct: row.querySelector('[data-field="leavening_type"]').value === "None" ? 0 : number(row.querySelector('[data-field="leavening_pct"]').value),
      flours: [...row.querySelectorAll('[data-pref-flours] [data-pref-flour]')].map((flourRowElement) => ({
        name: flourRowElement.querySelector('[data-field="name"]').value.trim(),
        pct: number(flourRowElement.querySelector('[data-field="pct"]').value),
        protein_pct: optionalNumber(flourRowElement.querySelector('[data-field="protein_pct"]').value),
        ash_pct: optionalNumber(flourRowElement.querySelector('[data-field="ash_pct"]').value)
      })).filter((flour) => flour.name),
      notes: row.querySelector('[data-field="notes"]').value.trim()
    })).filter((item) => item.name);
    const stageRows = document.querySelectorAll("#mix-stage-list .stage-row");
    if (stageRows.length) {
      mixStages = [...stageRows].map((row) => ({
        speed: row.querySelector('[data-field="speed"]').value.trim(),
        minutes: number(row.querySelector('[data-field="minutes"]').value),
        notes: row.querySelector('[data-field="notes"]').value.trim()
      }));
    } else if (document.querySelector("#mix-stage-list")) {
      mixStages = [];
    }
  }

  function serialize() {
    readRepeaters();
    document.querySelector("#flours-json").value = JSON.stringify(flours);
    document.querySelector("#ingredients-json").value = JSON.stringify(ingredients);
    document.querySelector("#preferments-json").value = JSON.stringify(preferments);
    const stagesField = document.querySelector("#mix-stages-json");
    if (stagesField) stagesField.value = JSON.stringify(mixStages);
  }

  function updateMixTime() {
    const target = document.querySelector("#total-mix-time");
    if (!target) return;
    const total = mixStages.reduce((sum, stage) => sum + number(stage.minutes), 0);
    target.textContent = `${Number.isInteger(total) ? total : total.toFixed(1)} min total`;
  }

  function normalizedBlend(items) {
    const total = items.reduce((sum, item) => sum + number(item.pct), 0);
    return {
      total,
      items: items.map((item) => ({ ...item, normalizedPct: total ? number(item.pct) * 100 / total : 0 }))
    };
  }

  function updatePreview() {
    readRepeaters();
    updateMixTime();
    const ballCount = Math.max(1, number(inputValue("ball_count"), 1));
    const ballWeight = number(inputValue("ball_weight_g"));
    const targetTotal = ballCount * ballWeight;
    const hydration = number(inputValue("hydration_pct"));
    const salt = number(inputValue("salt_pct"));
    const yeast = inputValue("yeast_type") === "None" ? 0 : number(inputValue("yeast_pct"));
    const ingredientPct = ingredients.reduce((sum, item) => sum + item.pct, 0);
    const matureStarterPct = preferments.filter((item) => item.leavening_type === "Mature Starter").reduce((sum, item) => sum + item.amount_pct * item.leavening_pct / 100, 0);
    const totalPct = 100 + hydration + salt + yeast + ingredientPct + matureStarterPct;
    const scaledTotal = targetTotal * (1 + number(inputValue("residue_pct")) / 100);
    const totalFlour = totalPct > 0 ? scaledTotal / (totalPct / 100) : 0;
    const totalWater = totalFlour * hydration / 100;
    const totalYeast = totalFlour * yeast / 100;
    const warnings = [];
    const finalBlend = normalizedBlend(flours);
    if (Math.abs(finalBlend.total - 100) > .05) warnings.push(`Final mix flour blend totals ${finalBlend.total.toFixed(2)}%, not 100%.`);

    let prefFlourTotal = 0;
    let prefWaterTotal = 0;
    let prefFormulaYeast = 0;
    const prefFlourRows = [];
    const prefTotalPct = preferments.reduce((sum, item) => sum + item.amount_pct, 0);
    preferments.forEach((pref) => {
      const componentTotal = pref.water_pct + pref.leavening_pct;
      const card = [...document.querySelectorAll(".preferment-editor")].find((node) => node.querySelector('[data-field="name"]')?.value.trim() === pref.name);
      const balance = card?.querySelector("[data-flour-balance]");
      if (balance) balance.textContent = `${Math.max(0, 100 - componentTotal).toFixed(3)}%`;
      if (componentTotal > 100) warnings.push(`${pref.name} water and leavening exceed 100%.`);
      const prefTotal = totalFlour * pref.amount_pct / 100;
      const prefWater = prefTotal * pref.water_pct / 100;
      const prefLeavening = prefTotal * pref.leavening_pct / 100;
      const prefFlour = Math.max(0, prefTotal - prefWater - prefLeavening);
      const prefBlend = normalizedBlend(pref.flours || []);
      if (Math.abs(prefBlend.total - 100) > .05) warnings.push(`${pref.name} flour blend totals ${prefBlend.total.toFixed(2)}%, not 100%.`);
      prefBlend.items.forEach((item) => prefFlourRows.push({ ...item, weight: prefFlour * item.normalizedPct / 100, source: pref.name }));
      prefFlourTotal += prefFlour;
      prefWaterTotal += prefWater;
      if (!["None", "Mature Starter"].includes(pref.leavening_type)) prefFormulaYeast += prefLeavening;
    });
    if (prefFlourTotal > totalFlour + .01) warnings.push("Preferments contain more flour than the complete formula.");
    if (prefWaterTotal > totalWater + .01) warnings.push("Preferments contain more water than the complete formula.");
    if (prefFormulaYeast > totalYeast + .01) warnings.push("Preferments contain more yeast than the complete formula.");

    const finalFlour = Math.max(0, totalFlour - prefFlourTotal);
    const finalFlourRows = finalBlend.items.map((item) => ({ ...item, weight: finalFlour * item.normalizedPct / 100, source: "Final Mix" }));
    const allFlourRows = [...finalFlourRows, ...prefFlourRows];
    const hasAllProtein = allFlourRows.length && allFlourRows.every((item) => item.protein_pct !== null);
    const hasAllAsh = allFlourRows.length && allFlourRows.every((item) => item.ash_pct !== null);
    const protein = hasAllProtein ? allFlourRows.reduce((sum, item) => sum + item.weight * item.protein_pct, 0) / totalFlour : null;
    const ash = hasAllAsh ? allFlourRows.reduce((sum, item) => sum + item.weight * item.ash_pct, 0) / totalFlour : null;
    if (!hasAllProtein) warnings.push("Enter protein for every flour to calculate overall protein.");
    if (!hasAllAsh) warnings.push("Enter ash for every flour to calculate overall ash.");

    document.querySelector("#preview-total").textContent = `${Math.round(targetTotal).toLocaleString()} g`;
    document.querySelector("#preview-ball").textContent = `${Math.round(ballWeight).toLocaleString()} g`;
    document.querySelector("#preview-flour").textContent = `${Math.round(totalFlour).toLocaleString()} g`;
    document.querySelector("#preview-pref").textContent = `${prefTotalPct.toFixed(2)}%`;
    document.querySelector("#preview-protein").textContent = protein === null ? "—" : `${protein.toFixed(2)}%`;
    document.querySelector("#preview-ash").textContent = ash === null ? "—" : `${ash.toFixed(2)}%`;
    document.querySelector("#preferment-total-summary").textContent = `${prefTotalPct.toFixed(2)}%`;
    document.querySelector("#preview-warnings").innerHTML = warnings.map((warning) => `<div class="preview-warning">${escapeHtml(warning)}</div>`).join("");

    const rows = [
      ...allFlourRows.map((item) => ({ name: `${item.name} · ${item.source}`, weight: item.weight })),
      { name: "Water", weight: totalWater },
      ...(yeast ? [{ name: inputValue("yeast_type"), weight: totalYeast }] : []),
      { name: "Salt", weight: totalFlour * salt / 100 },
      ...ingredients.map((item) => ({ name: item.name, weight: totalFlour * item.pct / 100 }))
    ];
    document.querySelector("#preview-table").innerHTML = rows.map((row) => `<div><span>${escapeHtml(row.name)}</span><strong>${row.weight < 10 ? row.weight.toFixed(2) : row.weight.toFixed(1)} g</strong></div>`).join("");
    serialize();
  }

  document.querySelector("#add-flour").addEventListener("click", () => {
    readRepeaters(); flours.push({ name: "", pct: 0, protein_pct: null, ash_pct: null }); renderFlours(); updatePreview();
  });
  document.querySelector("#add-ingredient").addEventListener("click", () => {
    readRepeaters(); ingredients.push({ name: "", pct: 0 }); renderIngredients(); updatePreview();
  });
  document.querySelector("#add-preferment").addEventListener("click", () => {
    readRepeaters();
    preferments.push({ name: "Poolish", type: "Poolish", amount_pct: 55, water_pct: 50, leavening_type: inputValue("yeast_type") || "IDY", leavening_pct: 0.01, flours: [{ name: "Poolish Flour", pct: 100, protein_pct: null, ash_pct: null }], notes: "" });
    renderPreferments(); updatePreview();
  });
  document.querySelector("#add-stage")?.addEventListener("click", () => {
    readRepeaters(); mixStages.push({ speed: "", minutes: 0, notes: "" }); renderMixStages(); updatePreview();
  });
  document.querySelector("#clear-rating")?.addEventListener("click", () => {
    form.querySelectorAll('[name="overall_rating"]').forEach((input) => input.checked = false);
  });

  form.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-remove-flour-row")) {
      const finalRow = button.closest("[data-final-flour]");
      if (finalRow) {
        readRepeaters(); flours.splice(number(finalRow.dataset.index), 1); renderFlours();
      } else {
        const prefCard = button.closest(".preferment-editor");
        const flourElement = button.closest("[data-pref-flour]");
        readRepeaters();
        preferments[number(prefCard.dataset.index)].flours.splice(number(flourElement.dataset.flourIndex), 1);
        renderPreferments();
      }
    }
    if (button.dataset.removeIngredient !== undefined) { readRepeaters(); ingredients.splice(number(button.dataset.removeIngredient), 1); renderIngredients(); }
    if (button.dataset.removePreferment !== undefined) { readRepeaters(); preferments.splice(number(button.dataset.removePreferment), 1); renderPreferments(); }
    if (button.dataset.addPrefFlour !== undefined) {
      readRepeaters(); preferments[number(button.dataset.addPrefFlour)].flours.push({ name: "", pct: 0, protein_pct: null, ash_pct: null }); renderPreferments();
    }
    if (button.dataset.removeStage !== undefined) { readRepeaters(); mixStages.splice(number(button.dataset.removeStage), 1); renderMixStages(); }
    updatePreview();
  });
  form.addEventListener("input", updatePreview);
  form.addEventListener("change", (event) => {
    const flourRowElement = event.target.closest(".flour-row");
    if (flourRowElement && event.target.matches('select[data-field="name"]')) {
      const selectedFlour = flourLibrary.find((flour) => flour.name === event.target.value);
      if (selectedFlour) {
        flourRowElement.querySelector('[data-field="protein_pct"]').value = selectedFlour.protein_pct;
        flourRowElement.querySelector('[data-field="ash_pct"]').value = selectedFlour.ash_pct;
      }
    }
    updatePreview();
  });
  form.addEventListener("submit", serialize);

  renderFlours();
  renderIngredients();
  renderPreferments();
  renderMixStages();
  updatePreview();
})();
