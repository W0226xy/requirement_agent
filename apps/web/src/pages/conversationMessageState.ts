import type { ConversationMessage } from "../types";

export const optimisticMessageKeyPrefix = "local-";

function sortMessages(messages: ConversationMessage[]) {
  return [...messages].sort(
    (left, right) => left.sequence_number - right.sequence_number,
  );
}

/**
 * A messages GET is authoritative for persisted messages, but it can race an
 * in-flight POST. Keep browser-only optimistic rows until that POST replaces
 * them with the persisted rows it returns.
 */
export function mergeServerMessages(
  current: ConversationMessage[],
  serverMessages: ConversationMessage[],
) {
  const serverKeys = new Set(serverMessages.map((item) => item.message_key));
  const optimistic = current.filter(
    (item) =>
      item.message_key.startsWith(optimisticMessageKeyPrefix) &&
      !serverKeys.has(item.message_key),
  );
  return sortMessages([...serverMessages, ...optimistic]);
}

export function upsertMessages(
  current: ConversationMessage[],
  updates: ConversationMessage[],
) {
  const byKey = new Map(current.map((item) => [item.message_key, item]));
  updates.forEach((item) => byKey.set(item.message_key, item));
  return sortMessages([...byKey.values()]);
}
