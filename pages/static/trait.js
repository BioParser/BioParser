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

function fill({ trait, iri, version, replaced_by: replacement }) {
  document.title = trait.name;
  slot("name", trait.name);
  slot("code", trait.code);
  slot("trait_id", trait.trait_id);
  slot("definition", trait.definition);
  slot("kind", trait.kind);
  slot("status", trait.status);
  document.querySelector("[data-slot=status]").hidden = trait.status !== "deprecated";
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
    document.getElementById("stored-verb").textContent = trait.kind === "count" ? "Counted as" : "Stored unit";
    slot("unit", trait.stored_unit ?? "");
  }
  document.getElementById("replaced-line").hidden = !replacement;
  if (replacement) {
    const link = document.getElementById("replaced");
    link.href = replacement.href;
    link.textContent = replacement.code;
    const since = document.getElementById("replaced-in");
    since.href = `../../${replacement.since}/`;
    since.textContent = replacement.since;
  }
  const back = document.getElementById("version-list");
  back.href = `../../${version}/`;
  back.textContent = version;
}

async function load(version) {
  const response = await fetch(`../../${version}/traits/${file}.json`);
  if (response.ok) fill(await response.json());
}

versions.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-version]");
  if (!chip) return;
  event.preventDefault();
  const version = chip.dataset.version;
  const url = new URL(location.href);
  url.searchParams.set("version", version);
  history.replaceState(null, "", url);
  selectVersion(version);
  load(version);
});

const wanted = new URLSearchParams(location.search).get("version");
if (wanted && chipFor(wanted)) {
  selectVersion(wanted);
  load(wanted);
}
