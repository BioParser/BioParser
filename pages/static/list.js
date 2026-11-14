// Filter the rows of a version list as you type. Domains with no match are hidden.
const input = document.getElementById("filter");
const domains = [...document.querySelectorAll(".domain")];
const none = document.getElementById("none");

input.addEventListener("input", () => {
  const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
  let shown = 0;
  for (const domain of domains) {
    let inDomain = 0;
    for (const row of domain.querySelectorAll("li[data-search]")) {
      const match = words.every((word) => row.dataset.search.includes(word));
      row.hidden = !match;
      if (match) inDomain += 1;
    }
    domain.hidden = inDomain === 0;
    shown += inDomain;
  }
  none.hidden = shown !== 0;
});
