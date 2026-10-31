// Version switch for a trait page. The page is rendered for the latest version. Choosing
// another version fetches that version's small JSON file for this trait and fills the slots.
const select = document.getElementById("version");
const page = document.getElementById("trait");
const file = page.dataset.id.replace(":", "-");

function slot(name, value) {
  for (const element of document.querySelectorAll(`[data-slot="${name}"]`)) {
    element.textContent = value;
    element.dataset.value = value;
  }
}

function fill({ trait, iri, version, replaced_by: replacement }) {
  document.title = trait.name;
  slot("name", trait.name);
  slot("trait_id", trait.trait_id);
  slot("definition", trait.definition);
  slot("kind", trait.kind);
  slot("status", trait.status);
  slot("domain", trait.domain);
  slot("iri", iri);
  const levels = document.getElementById("levels");
  levels.replaceChildren(
    ...(trait.levels ?? []).map((level) => {
      const item = document.createElement("li");
      item.textContent = level;
      return item;
    }),
  );
  document.getElementById("unit-line").hidden = "levels" in trait;
  slot("unit", trait.stored_unit ?? "");
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

select.addEventListener("change", () => {
  const url = new URL(location.href);
  url.searchParams.set("version", select.value);
  history.replaceState(null, "", url);
  load(select.value);
});

const wanted = new URLSearchParams(location.search).get("version");
if (wanted && [...select.options].some((option) => option.value === wanted)) {
  select.value = wanted;
  load(wanted);
}
