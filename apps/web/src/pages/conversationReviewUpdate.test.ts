import { describe, expect, it } from "vitest";

import { applyReviewTaskUpdate } from "./conversationReviewUpdate";
import type { ConversationMessage, ReviewTask } from "../types";

const review = {
  id: 7,
  source_record_id: 12,
  review_status: "rejected",
} as ReviewTask;

it("shows a rejected review immediately after the successful review response", () => {
  const messages = [{
    message_key: "MSG-1",
    source: { id: 12, processing_status: "pending_review" },
    review_task: null,
  }] as ConversationMessage[];

  const updated = applyReviewTaskUpdate(messages, review);

  expect(updated[0].review_task?.review_status).toBe("rejected");
  expect(updated[0].source?.processing_status).toBe("rejected");
});
