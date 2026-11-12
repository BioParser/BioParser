import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";
import { ApiError, type ExtractionArtifact, type ParserArtifact } from "../api/client";
import { artifactError, EvidenceView } from "./EvidenceView";
import type { Observation } from "./quotation";

// Vitest globals are off, so Testing Library cannot register its cleanup by itself.
afterEach(cleanup);

// jsdom has no layout, so it does not implement scrollIntoView.
const scrollIntoView = vi.fn<Element["scrollIntoView"]>();

beforeEach(() => {
  stubArtifacts();
  Element.prototype.scrollIntoView = scrollIntoView;
});

afterEach(() => {
  vi.unstubAllGlobals();
  scrollIntoView.mockReset();
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

function citation(text: string, ...evidence: [string, number, number][]) {
  return {
    kind: "citation" as const,
    text,
    evidence: evidence.map(([block_id, start, end]) => ({ block_id, start, end })),
  };
}

function observation(
  value: ReturnType<typeof citation>,
  unit: ReturnType<typeof citation>,
): Observation {
  return {
    trait: { kind: "interpretation", value: "body_mass", passage: value },
    verbatimScientificName: value,
    verbatimTraitValue: value,
    verbatimTraitUnit: unit,
  };
}

function textBlock(block_id: string, text: string) {
  return {
    block_id,
    order: 0,
    kind: "text" as const,
    role: "body" as const,
    content: { type: "text" as const, text },
  };
}

const PARSER: ParserArtifact = {
  schema_version: "1",
  checksum: "abc",
  parser: { name: "default", version: "1", configuration: {} },
  pages: [
    {
      page_number: 1,
      width: 612,
      height: 792,
      blocks: [textBlock("b0", "Mus musculus weighed 12.5 kg")],
    },
    {
      page_number: 2,
      width: 612,
      height: 792,
      blocks: [textBlock("b3", "The mean was 1"), textBlock("b4", "4 g in adults")],
    },
  ],
};

const EXTRACTION: ExtractionArtifact = {
  schema_version: "1",
  extractor: "default",
  extractor_version: "1",
  observations: [
    observation(citation("12.5", ["b0", 21, 25]), citation("kg", ["b0", 26, 28])),
    observation(citation("14", ["b3", 13, 14], ["b4", 0, 1]), citation("g", ["b4", 2, 3])),
    observation(citation("12.5", ["b9", 0, 4]), citation("kg", ["b9", 5, 7])),
  ],
};

function error(status: number, code: string): Response {
  return Response.json({ detail: { code, message: code } }, { status });
}

function stubArtifacts(
  parser: () => Response = () => Response.json(PARSER),
  extraction: () => Response = () => Response.json(EXTRACTION),
): Mock<typeof fetch> {
  const fetchMock = vi.fn<typeof fetch>(async (input) =>
    String(input).endsWith("/parser") ? parser() : extraction(),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// A results row that opens the view, so focus has somewhere to return to.
function Harness({ index }: { index: number }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Row
      </button>
      {open && (
        <EvidenceView jobId="job-1" observationIndex={index} onClose={() => setOpen(false)} />
      )}
    </>
  );
}

function renderView(index = 0) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <Harness index={index} />
    </QueryClientProvider>,
  );
  const row = screen.getByRole("button", { name: "Row" });
  row.focus();
  fireEvent.click(row);
  return {
    row,
    rerender: (next: number) =>
      view.rerender(
        <QueryClientProvider client={queryClient}>
          <Harness index={next} />
        </QueryClientProvider>,
      ),
  };
}

describe("EvidenceView", () => {
  it("shows the quotation with the value and the unit marked", async () => {
    renderView(0);
    const quote = await screen.findByRole("blockquote");
    expect(quote.textContent).toBe("Mus musculus weighed 12.5 kg");
    const marks = quote.querySelectorAll("mark");
    expect([...marks].map((mark) => [mark.textContent, mark.className])).toEqual([
      ["12.5", "evidence-value"],
      ["kg", "evidence-unit"],
    ]);
    expect(screen.getByText(/page 1/).textContent).toBe("Block b0, page 1");
  });

  it("labels the value and the unit in words, not only by color", async () => {
    renderView(0);
    await screen.findByRole("blockquote");
    expect(screen.getByText("Value").nextElementSibling?.textContent).toBe("12.5");
    expect(screen.getByText("Unit").nextElementSibling?.textContent).toBe("kg");
  });

  it("shows both blocks of a value that continues into a second block", async () => {
    renderView(1);
    await screen.findAllByRole("blockquote");
    const captions = screen.getAllByText(/^Block/).map((caption) => caption.textContent);
    expect(captions).toEqual(["Block b3, page 2", "Block b4, page 2"]);
  });

  it("says when the observation does not exist", async () => {
    renderView(7);
    expect((await screen.findByRole("alert")).textContent).toBe(
      "This job has no observation 8. It has 3.",
    );
  });

  it("says when the extraction results do not match the parsed text", async () => {
    renderView(2);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "The extraction results do not match the parsed text.",
    );
  });

  it("says the extraction results are missing", async () => {
    stubArtifacts(undefined, () => error(409, "artifact_not_available"));
    renderView(0);
    expect((await screen.findByRole("alert")).textContent).toBe(
      "This job has no extraction results yet. They are stored once the job is done.",
    );
  });

  it("keeps the value and the unit when the parsed text cannot be read", async () => {
    stubArtifacts(() => error(500, "artifact_invalid"));
    renderView(0);
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The stored parsed text of this job cannot be read.",
    );
    expect(screen.getByText("Value").nextElementSibling?.textContent).toBe("12.5");
    expect(screen.queryByRole("blockquote")).toBeNull();
  });

  it("moves focus to the view when it opens", async () => {
    renderView(0);
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("heading", { level: 2 })),
    );
  });

  it("returns focus to the row on close", async () => {
    const { row } = renderView(0);
    fireEvent.click(await screen.findByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region")).toBeNull();
    expect(document.activeElement).toBe(row);
  });

  it("closes on Escape", async () => {
    const { row } = renderView(0);
    fireEvent.keyDown(await screen.findByRole("heading", { level: 2 }), { key: "Escape" });
    expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
    expect(document.activeElement).toBe(row);
  });

  it("scrolls the value into view", async () => {
    renderView(0);
    await screen.findByRole("blockquote");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
    const target = scrollIntoView.mock.contexts.at(-1);
    expect(target).toBeInstanceOf(HTMLElement);
    expect((target as HTMLElement).className).toBe("evidence-value");
    expect((target as HTMLElement).closest("blockquote")).not.toBeNull();
  });

  it("scrolls to the value of the next observation", async () => {
    const { rerender } = renderView(0);
    await screen.findByRole("blockquote");
    rerender(1);
    await screen.findAllByText(/page 2/);
    await waitFor(() =>
      expect((scrollIntoView.mock.contexts.at(-1) as HTMLElement).textContent).toBe("1"),
    );
  });

  it("does not scroll when there is no quotation", async () => {
    stubArtifacts(() => error(500, "artifact_invalid"));
    renderView(0);
    await screen.findByRole("alert");
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("reuses the artifacts for another observation of the job", async () => {
    const fetchMock = stubArtifacts();
    const { rerender } = renderView(0);
    await screen.findByRole("blockquote");
    rerender(1);
    await screen.findAllByText(/page 2/);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("artifactError", () => {
  it("says what is missing for a 409", () => {
    expect(artifactError(new ApiError(409, "artifact_not_available", "x"), "parser")).toBe(
      "This job has no parsed text yet. It is stored once the PDF is parsed.",
    );
  });

  it("says the job does not exist for a 404", () => {
    expect(artifactError(new ApiError(404, "job_not_found", "x"), "extraction")).toBe(
      "This job does not exist.",
    );
  });

  it("uses the API's message for another error", () => {
    expect(
      artifactError(new ApiError(503, "storage_unavailable", "Storage is down"), "parser"),
    ).toBe("Storage is down");
  });

  it("says the connection failed for a network error", () => {
    expect(artifactError(new TypeError("Failed to fetch"), "parser")).toBe(
      "The parsed text could not be loaded. Check the connection and reopen the observation.",
    );
  });
});