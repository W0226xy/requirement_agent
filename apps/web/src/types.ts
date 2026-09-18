export type UserSession = {
  actorId: string;
  actorName: string;
  role: "reviewer" | "admin";
};

export type PageResponse<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};

export type Attachment = {
  id: number;
  file_name: string;
  file_type: string;
  file_size: number;
  storage_path: string;
  ocr_text: string | null;
  parsed_text: string | null;
  parse_status: string;
  error_message: string | null;
  created_at: string;
};

export type SourceRecord = {
  id: number;
  source_key: string;
  channel_type: string;
  external_event_id: string;
  submitter_id: string;
  submitter_name: string;
  raw_text: string;
  raw_metadata: Record<string, unknown>;
  received_at: string;
  processing_status: string;
  created_at: string;
  attachments: Attachment[];
};

export type Conversation = {
  conversation_key: string;
  owner_id: string;
  title: string;
  title_is_custom: boolean;
  summary: string;
  business_context: Record<string, unknown>;
  memory_revision: number;
  memory_covered_sequence: number;
  created_at: string;
  updated_at: string;
};

export type ConversationMessage = {
  message_key: string;
  sequence_number: number;
  role: string;
  created_at: string;
  source: SourceRecord | null;
  content: string;
  tool_calls: { tool_name: string; ok: boolean; summary: string }[];
  references: Record<string, unknown>[];
  chat_status: string;
  latest_extraction: Record<string, unknown> | null;
  latest_conflict_analysis: Record<string, unknown> | null;
  review_task: ReviewTask | null;
};

export type CreateConversationMessageResponse = {
  message: ConversationMessage;
  replayed: boolean;
  intent: string;
  assistant_message: ConversationMessage | null;
};

export type ChatQueryResponse = {
  answer: string;
  tool_calls: { tool_name: string; ok: boolean; summary: string }[];
  references: Record<string, unknown>[];
};

export type FeatureLineage = {
  source_record_id: number;
  introduced_version_id: number;
  operation_type: string;
  evidence_text: string;
  created_at: string;
};

export type RequirementFeature = {
  feature_key: string;
  module: string;
  feature_title: string;
  feature_description: string;
  acceptance_criteria: string[];
  feature_status: string;
  sort_order: number;
  lineage: FeatureLineage[];
};

export type Requirement = {
  id: number;
  requirement_key: string;
  title: string;
  current_version_id: number | null;
  current_version_number: number | null;
  status: string;
  functional_modules: string[];
  extra_fields: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  features: RequirementFeature[];
};

export type RequirementVersion = {
  id: number;
  requirement_id: number;
  version_number: number;
  parent_version_id: number | null;
  change_type: string;
  version_title: string;
  requirement_snapshot: Record<string, unknown>;
  diff_snapshot: { operations?: ChangeItem[] };
  change_reason: string;
  created_by: string;
  reviewed_by: string;
  created_at: string;
  features: RequirementFeature[];
};

export type FeatureContent = {
  module: string;
  feature_title: string;
  feature_description: string;
  acceptance_criteria: string[];
};

export type ProposedOperation = {
  operation: "add" | "modify" | "delete" | "restore";
  feature_key: string | null;
  content: FeatureContent | null;
  source_record_id: number;
  reason: string;
};

export type ChangeItem = {
  operation: string;
  feature_key: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string;
};

export type ReviewTask = {
  id: number;
  source_record_id: number;
  target_requirement_id: number | null;
  analysis_result_id: number;
  review_status: string;
  decision: string | null;
  extraction_snapshot: Record<string, unknown>;
  candidate_snapshot: Record<string, unknown>[];
  analysis_snapshot: {
    conflict_status?: string;
    conflicts?: Record<string, unknown>[];
    risks?: Record<string, unknown>[];
    clarification_questions?: string[];
    proposed_operations?: ProposedOperation[];
  };
  approved_operations: ProposedOperation[] | null;
  reviewer_id: string | null;
  review_comment: string | null;
  reviewed_at: string | null;
  committed_version_id: number | null;
  created_at: string;
  updated_at: string;
};

export type Candidate = {
  requirement_key: string;
  version_number: number;
  title: string;
  functional_modules: string[];
  features: RequirementFeature[];
  similarity_score: number;
  matched_text: string;
  sources: Record<string, unknown>[];
};

export type AnalysisResult = {
  id: number;
  source_record_id: number;
  analysis_type: string;
  model_name: string;
  prompt_version: string;
  input_snapshot: Record<string, unknown>;
  result_json: Record<string, unknown> | null;
  raw_output: string | null;
  confidence: number | null;
  duration_ms: number;
  error_message: string | null;
  attempt_number: number;
  created_at: string;
};
