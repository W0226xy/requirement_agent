import { describe, expect, it } from "vitest";

import type { ConversationMessage } from "../types";
import { mergeServerMessages, upsertMessages } from "./conversationMessageState";

function chatMessage(key: string, sequence: number): ConversationMessage {
  return {
    message_key: key,
    sequence_number: sequence,
    role: key.includes("assistant") ? "assistant" : "user",
    created_at: "2026-09-28T00:00:00Z",
    source: null,
    content: "",
    tool_calls: [],
    references: [],
    chat_status: "pending",
    latest_extraction: null,
    latest_conflict_analysis: null,
    review_task: null,
  };
}

describe("conversation message state", () => {
  it("does not let a stale poll remove an optimistic turn", () => {
    const previous = chatMessage("MSG-1", 1);
    const optimisticUser = chatMessage("local-user-1", Number.MAX_SAFE_INTEGER - 1);
    const optimisticAssistant = chatMessage("local-assistant-1", Number.MAX_SAFE_INTEGER);

    expect(
      mergeServerMessages(
        [previous, optimisticUser, optimisticAssistant],
        [previous],
      ).map((item) => item.message_key),
    ).toEqual(["MSG-1", "local-user-1", "local-assistant-1"]);
  });

  it("upserts the persisted POST response without dropping prior messages", () => {
    const previous = chatMessage("MSG-1", 1);
    const persistedUser = chatMessage("MSG-2", 2);
    const persistedAssistant = chatMessage("MSG-3", 3);

    expect(
      upsertMessages([previous], [persistedUser, persistedAssistant]).map(
        (item) => item.message_key,
      ),
    ).toEqual(["MSG-1", "MSG-2", "MSG-3"]);
  });
});
