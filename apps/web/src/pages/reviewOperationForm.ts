import type { FeatureContent, ProposedOperation, RequirementFeature } from "../types";

export type OperationKind = ProposedOperation["operation"];
export type DraftField = "module" | "featureTitle" | "featureDescription" | "acceptanceCriteria";
export type FieldDirtyState = Record<DraftField, boolean>;

export type OperationFormValue = {
  id: string;
  operation: OperationKind;
  featureKey: string | null;
  module: string;
  featureTitle: string;
  featureDescription: string;
  acceptanceCriteria: string[];
  reason: string;
  /** Recomputable AI/extraction/target suggestion; never submitted directly. */
  suggestedContent: FeatureContent;
  dirty: FieldDirtyState;
};

export const changeOperationOptions: { value: OperationKind; label: string }[] = [
  { value: "add", label: "新增" }, { value: "modify", label: "修改" },
  { value: "delete", label: "删除" }, { value: "restore", label: "恢复" },
];

const contentDefaults: FeatureContent = {
  module: "", feature_title: "", feature_description: "", acceptance_criteria: [],
};
const cleanDirty = (): FieldDirtyState => ({
  module: false, featureTitle: false, featureDescription: false, acceptanceCriteria: false,
});

export function proposedOperationsToFormValues(
  operations: ProposedOperation[] | undefined,
  extractionSnapshot: Record<string, unknown> = {},
): OperationFormValue[] {
  return (operations ?? []).map((item, index) => recalculateSuggestedDraft({
    id: `ai-${index}`, operation: item.operation, featureKey: item.feature_key,
    module: "", featureTitle: "", featureDescription: "", acceptanceCriteria: [],
    reason: item.reason ?? "", suggestedContent: item.content ?? contentDefaults, dirty: cleanDirty(),
  }, [], extractionSnapshot, true));
}

export function newOperationFormValue(
  extractionSnapshot: Record<string, unknown> = {},
): OperationFormValue {
  return recalculateSuggestedDraft({
    id: crypto.randomUUID(), operation: "add", featureKey: null,
    module: "", featureTitle: "", featureDescription: "", acceptanceCriteria: [], reason: "",
    suggestedContent: contentDefaults, dirty: cleanDirty(),
  }, [], extractionSnapshot, true);
}

/** Recompute post-change state; target data is the final fallback only. */
export function recalculateSuggestedDraft(
  value: OperationFormValue,
  features: RequirementFeature[],
  extractionSnapshot: Record<string, unknown>,
  force = false,
): OperationFormValue {
  const target = features.find((item) => item.feature_key === value.featureKey);
  const suggestion = suggestionFor(value.operation, value.suggestedContent, extractionSnapshot, target);
  if (value.operation === "delete") return { ...value, suggestedContent: suggestion };
  return {
    ...value, suggestedContent: suggestion,
    module: force || !value.dirty.module ? suggestion.module : value.module,
    featureTitle: force || !value.dirty.featureTitle ? suggestion.feature_title : value.featureTitle,
    featureDescription: force || !value.dirty.featureDescription
      ? suggestion.feature_description : value.featureDescription,
    acceptanceCriteria: force || !value.dirty.acceptanceCriteria
      ? suggestion.acceptance_criteria : value.acceptanceCriteria,
    dirty: force ? cleanDirty() : value.dirty,
  };
}

export function applyTargetFeature(
  value: OperationFormValue,
  featureKey: string,
  features: RequirementFeature[],
  extractionSnapshot: Record<string, unknown> = {},
): OperationFormValue {
  return recalculateSuggestedDraft({ ...value, featureKey }, features, extractionSnapshot);
}

function suggestionFor(
  operation: OperationKind,
  proposed: FeatureContent,
  extraction: Record<string, unknown>,
  target: RequirementFeature | undefined,
): FeatureContent {
  const extracted = extractionToContent(extraction);
  const existing = target && operation !== "add" ? {
    module: target.module, feature_title: target.feature_title,
    feature_description: target.feature_description, acceptance_criteria: target.acceptance_criteria,
  } : undefined;
  return {
    module: firstText(proposed.module, extracted.module, existing?.module),
    feature_title: firstText(proposed.feature_title, extracted.feature_title, existing?.feature_title),
    feature_description: firstText(
      proposed.feature_description, extracted.feature_description, existing?.feature_description,
    ),
    acceptance_criteria: firstCriteria(
      proposed.acceptance_criteria, extracted.acceptance_criteria, existing?.acceptance_criteria,
    ),
  };
}

function extractionToContent(extraction: Record<string, unknown>): FeatureContent {
  const modules = stringArray(extraction.functional_modules);
  return {
    module: modules[0] ?? "", feature_title: stringValue(extraction.requirement_summary),
    feature_description: stringValue(extraction.requirement_description),
    acceptance_criteria: stringArray(extraction.acceptance_criteria),
  };
}

function firstText(...values: (string | undefined)[]): string {
  return values.find((item) => item?.trim())?.trim() ?? "";
}
function firstCriteria(...values: (string[] | undefined)[]): string[] {
  return values.find((item) => item?.length)?.map((item) => item.trim()).filter(Boolean) ?? [];
}
function stringValue(value: unknown): string { return typeof value === "string" ? value : ""; }
function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export type OperationValidation = { operations?: ProposedOperation[]; errors: Record<string, string> };

export function buildApprovalRequest(
  decision: "create" | "merge", title: string,
  target: { id: number; requirement_key: string; current_version_number: number | null } | undefined,
  operations: ProposedOperation[], comment: string,
) {
  return {
    decision, title: decision === "create" ? title : null,
    target_requirement_key: decision === "merge" ? target?.requirement_key ?? null : null,
    expected_requirement_id: decision === "merge" ? target?.id ?? null : null,
    expected_current_version: decision === "merge" ? target?.current_version_number ?? null : null,
    operations, comment: comment || null,
  };
}

export function buildOperations(values: OperationFormValue[], sourceRecordId: number): OperationValidation {
  const errors: Record<string, string> = {};
  const operations = values.map((value) => {
    if (!value.reason.trim()) errors[`${value.id}.reason`] = "请填写变更原因";
    if (value.operation !== "add" && !value.featureKey) errors[`${value.id}.featureKey`] = "请选择目标功能";
    if (value.operation !== "delete") {
      if (!value.module.trim()) errors[`${value.id}.module`] = "请填写功能模块";
      if (!value.featureTitle.trim()) errors[`${value.id}.featureTitle`] = "请填写功能标题";
      if (!value.featureDescription.trim()) errors[`${value.id}.featureDescription`] = "请填写功能描述";
    }
    return {
      operation: value.operation, feature_key: value.operation === "add" ? null : value.featureKey,
      content: value.operation === "delete" ? null : {
        module: value.module.trim(), feature_title: value.featureTitle.trim(),
        feature_description: value.featureDescription.trim(),
        acceptance_criteria: value.acceptanceCriteria.map((item) => item.trim()).filter(Boolean),
      }, source_record_id: sourceRecordId, reason: value.reason.trim(),
    } satisfies ProposedOperation;
  });
  if (!values.length) errors.operations = "请至少新增一条变更操作";
  return Object.keys(errors).length ? { errors } : { operations, errors: {} };
}
