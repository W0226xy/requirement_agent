import type { ConversationMessage, ReviewTask } from "../types";

const sourceStatusForReview: Partial<Record<string, string>> = {
  rejected: "rejected",
  returned: "returned",
};

/** Apply the successful review response before the background refresh completes. */
export function applyReviewTaskUpdate(
  messages: ConversationMessage[],
  review: ReviewTask,
): ConversationMessage[] {
  const processingStatus = sourceStatusForReview[review.review_status];
  return messages.map((item) =>
    item.source?.id === review.source_record_id
      ? {
          ...item,
          source: processingStatus
            ? { ...item.source, processing_status: processingStatus }
            : item.source,
          review_task: review,
        }
      : item,
  );
}
