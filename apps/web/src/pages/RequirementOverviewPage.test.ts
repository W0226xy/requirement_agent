import { describe, expect, it } from "vitest";

import { requirementOverviewPath } from "./RequirementOverviewPage";

describe("requirementOverviewPath", () => {
  it("passes all overview filters to the dedicated endpoint", () => {
    expect(requirementOverviewPath("下载", "archived", "音乐")).toBe(
      "/api/v1/requirements/overview?keyword=%E4%B8%8B%E8%BD%BD&status=archived&module=%E9%9F%B3%E4%B9%90",
    );
  });
});
