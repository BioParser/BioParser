import { describe, expect, it } from "vitest";
import openapiText from "../../openapi.json?raw";
import { ETS_COLUMNS } from "./etsColumns";

// openapi.json is generated from the API (npm run gen:api) and CI checks it is current.
const openapi = JSON.parse(openapiText) as {
  components: { schemas: { EtsRow: { properties: Record<string, unknown> } } };
};

describe("ETS_COLUMNS", () => {
  it("lists every EtsRow column in the order the API serializes them", () => {
    expect([...ETS_COLUMNS]).toEqual(Object.keys(openapi.components.schemas.EtsRow.properties));
  });
});
