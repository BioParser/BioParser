// Version switch for a trait page. The page is rendered for the latest version. Choosing
// another version fetches that version's small JSON file for this trait and fills the slots.
const versions = document.getElementById("version");
const page = document.getElementById("trait");
const file = page.dataset.id.replace(":", "-");

function slot(name, value) {
  for (const element of document.querySelectorAll(`[data-slot="${name}"]`)) {
    element.textContent = value;
    element.dataset.value = value;
  }
}

function chipFor(version) {
  return [...versions.querySelectorAll("[data-version]")].find(
    (chip) => chip.dataset.version === version,
  );
}

function selectVersion(version) {
  for (const chip of versions.querySelectorAll("[data-version]")) {
    chip.classList.toggle("on", chip.dataset.version === version);
  }
}

function domainLabel(name) {
  const words = name.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function fill({ trait, iri, version }) {
  document.title = trait.name;
  slot("name", trait.name);
  slot("code", trait.code);
  slot("trait_id", trait.trait_id);
  slot("definition", trait.definition);
  slot("kind", trait.kind);
  slot("iri", iri);
  const domainLink = document.getElementById("domain-link");
  domainLink.textContent = domainLabel(trait.domain);
  domainLink.href = `../../${version}/#${trait.domain}`;
  const levels = document.getElementById("levels");
  levels.replaceChildren(
    ...(trait.levels ?? []).map((level) => {
      const item = document.createElement("li");
      item.textContent = level;
      return item;
    }),
  );
  const approximate = new Set(trait.approximate_units ?? []);
  document.getElementById("unit-rows").replaceChildren(
    ...Object.entries(trait.printed_units ?? {}).map(([unit, factor]) => {
      const row = document.createElement("tr");
      const unitCell = document.createElement("td");
      unitCell.className = "mono";
      unitCell.textContent = unit;
      const factorCell = document.createElement("td");
      factorCell.className = "mono";
      factorCell.append(`× ${factor}`);
      if (approximate.has(unit)) {
        const mark = document.createElement("span");
        mark.className = "approx";
        mark.textContent = "approximate";
        factorCell.append(mark);
      }
      row.append(unitCell, factorCell);
      return row;
    }),
  );
  const categorical = trait.kind === "categorical";
  document.getElementById("stored-line").hidden = categorical;
  document.getElementById("units-block").hidden = trait.kind !== "numeric";
  document.getElementById("levels-line").hidden = !categorical;
  if (!categorical) {
    document.getElementById("stored-verb").textContent = trait.kind === "count" ? "Counted as" : "Trait unit";
    slot("unit", trait.trait_unit ?? "");
  }
  const back = document.getElementById("version-list");
  back.href = `../../${version}/`;
  back.textContent = version;
}

const notice = document.getElementById("version-error");
let shown = document.querySelector("#version .chip.on")?.dataset.version ?? "";
let request = null;

function setAddress(version) {
  const url = new URL(location.href);
  url.searchParams.set("version", version);
  history.replaceState(null, "", url);
}

function fail(version) {
  selectVersion(shown);
  setAddress(shown);
  notice.hidden = false;
  notice.textContent = `Version ${version} did not load. This page still shows ${shown}.`;
}

async function load(version) {
  request?.abort();
  const controller = new AbortController();
  request = controller;
  notice.hidden = true;
  try {
    const response = await fetch(`../../${version}/traits/${file}.json`, {
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(String(response.status));
    const payload = await response.json();
    if (request !== controller) return;
    fill(payload);
    shown = version;
  } catch (error) {
    if (error.name === "AbortError" || request !== controller) return;
    fail(version);
  }
}

versions.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-version]");
  if (!chip) return;
  event.preventDefault();
  const version = chip.dataset.version;
  selectVersion(version);
  setAddress(version);
  load(version);
});

const wanted = new URLSearchParams(location.search).get("version");
if (wanted && chipFor(wanted)) {
  selectVersion(wanted);
  load(wanted);
}
