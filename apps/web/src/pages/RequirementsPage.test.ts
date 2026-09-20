import { describe, expect, it } from "vitest";

import { requirementListPath } from "./RequirementsPage";

describe("requirementListPath", () => {
  it("includes the selected functional module in the list request", () => {
    expect(requirementListPath(1, "播放器", "active", "音乐播放器")).toBe(
      "/api/v1/requirements?page=1&page_size=20&keyword=%E6%92%AD%E6%94%BE%E5%99%A8&status=active&module=%E9%9F%B3%E4%B9%90%E6%92%AD%E6%94%BE%E5%99%A8",
    );
  });
});
