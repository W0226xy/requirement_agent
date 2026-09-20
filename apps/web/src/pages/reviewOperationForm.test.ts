import { describe, expect, it } from "vitest";

import {
  applyTargetFeature,
  buildApprovalRequest,
  buildOperations,
  proposedOperationsToFormValues,
  recalculateSuggestedDraft,
} from "./reviewOperationForm";

describe("review operation form", () => {
  it("converts AI proposed operations into editable initial values", () => {
    const values = proposedOperationsToFormValues([{
      operation: "add",
      feature_key: null,
      source_record_id: 8,
      reason: "补充导出能力",
      content: {
        module: "报表",
        feature_title: "导出 CSV",
        feature_description: "支持导出 CSV 文件",
        acceptance_criteria: ["可下载 CSV"],
      },
    }]);
    expect(values[0]).toMatchObject({ operation: "add", module: "报表", reason: "补充导出能力" });
    expect(buildOperations(values, 99).operations).toEqual([{
      operation: "add",
      feature_key: null,
      source_record_id: 99,
      reason: "补充导出能力",
      content: {
        module: "报表",
        feature_title: "导出 CSV",
        feature_description: "支持导出 CSV 文件",
        acceptance_criteria: ["可下载 CSV"],
      },
    }]);
  });

  it("uses the selected target feature key and pre-fills modification content", () => {
    const value = proposedOperationsToFormValues([{
      operation: "modify", feature_key: null, source_record_id: 1, reason: "修订", content: {
        module: "", feature_title: "", feature_description: "", acceptance_criteria: [],
      },
    }])[0];
    const selected = applyTargetFeature(value, "FEAT-001", [{
      feature_key: "FEAT-001", module: "报表", feature_title: "导出 PDF",
      feature_description: "导出 PDF 文件", acceptance_criteria: ["可下载 PDF"],
      feature_status: "active", sort_order: 1, lineage: [],
    }]);
    expect(buildOperations([selected], 9).operations?.[0]).toMatchObject({
      operation: "modify", feature_key: "FEAT-001", source_record_id: 9,
      content: { feature_title: "导出 PDF" },
    });
  });

  it("prefers AI post-change content over the selected feature's obsolete state", () => {
    const [value] = proposedOperationsToFormValues([{
      operation: "modify", feature_key: "FEAT-MAINT", source_record_id: 1, reason: "入口迁移",
      content: {
        module: "车辆中心", feature_title: "车辆保养提醒入口调整为车辆中心展示",
        feature_description: "保养提醒入口仅在车辆中心页面展示，首页不再展示；用户仍可查看下次保养日期、剩余保养里程和推荐保养项目。",
        acceptance_criteria: [
          "车辆中心页面正常展示“保养提醒”入口", "首页不展示“保养提醒”入口",
          "用户进入后可查看下次保养日期", "用户可查看剩余保养里程和推荐保养项目",
        ],
      },
    }], {
      requirement_summary: "错误的回退标题",
      requirement_description: "错误的回退描述",
      acceptance_criteria: ["错误的旧标准"],
    });
    const selected = applyTargetFeature(value, "FEAT-MAINT", [{
      feature_key: "FEAT-MAINT", module: "首页", feature_title: "首页展示保养提醒入口",
      feature_description: "首页展示保养提醒入口", acceptance_criteria: ["首页正常展示保养提醒入口"],
      feature_status: "active", sort_order: 1, lineage: [],
    }]);

    expect(selected.featureTitle).toBe("车辆保养提醒入口调整为车辆中心展示");
    expect(selected.featureDescription).toContain("仅在车辆中心页面展示");
    expect(selected.featureDescription).not.toContain("首页展示保养提醒入口");
    expect(selected.acceptanceCriteria).toEqual([
      "车辆中心页面正常展示“保养提醒”入口", "首页不展示“保养提醒”入口",
      "用户进入后可查看下次保养日期", "用户可查看剩余保养里程和推荐保养项目",
    ]);
  });

  it("protects manually edited fields until the reviewer explicitly refills", () => {
    const [initial] = proposedOperationsToFormValues([{
      operation: "modify", feature_key: "FEAT-1", source_record_id: 1, reason: "调整", content: {
        module: "车辆", feature_title: "新标题", feature_description: "AI 新描述", acceptance_criteria: ["AI 标准"],
      },
    }]);
    const manuallyEdited = {
      ...initial,
      featureDescription: "审核人手动描述",
      dirty: { ...initial.dirty, featureDescription: true },
    };
    const refreshed = recalculateSuggestedDraft(manuallyEdited, [], {
      requirement_description: "异步加载的描述不应覆盖人工输入",
    });
    const refilled = recalculateSuggestedDraft(refreshed, [], {}, true);

    expect(refreshed.featureDescription).toBe("审核人手动描述");
    expect(refilled.featureDescription).toBe("AI 新描述");
    expect(refilled.dirty.featureDescription).toBe(false);
  });

  it("generates delete with null content and blocks invalid forms in Chinese", () => {
    const [value] = proposedOperationsToFormValues([{
      operation: "delete", feature_key: "FEAT-002", source_record_id: 1, reason: "移除过期功能", content: null,
    }]);
    expect(buildOperations([value], 7).operations?.[0]).toMatchObject({
      operation: "delete", feature_key: "FEAT-002", content: null, source_record_id: 7,
    });
    const invalid = buildOperations([{ ...value, featureKey: null, reason: "" }], 7);
    expect(invalid.operations).toBeUndefined();
    expect(invalid.errors[`${value.id}.featureKey`]).toBe("请选择目标功能");
    expect(invalid.errors[`${value.id}.reason`]).toBe("请填写变更原因");
    expect(buildOperations([], 7).errors.operations).toBe("请至少新增一条变更操作");
  });

  it("submits reviewer-edited values rather than the AI suggestion", () => {
    const [value] = proposedOperationsToFormValues([{
      operation: "add", feature_key: null, source_record_id: 1, reason: "新增", content: {
        module: "车辆", feature_title: "AI 标题", feature_description: "AI 描述", acceptance_criteria: ["AI 标准"],
      },
    }]);
    const edited = {
      ...value,
      featureTitle: "审核人标题",
      acceptanceCriteria: ["审核人验收标准"],
      dirty: { ...value.dirty, featureTitle: true, acceptanceCriteria: true },
    };
    expect(buildOperations([edited], 9).operations?.[0].content).toMatchObject({
      feature_title: "审核人标题", acceptance_criteria: ["审核人验收标准"],
    });
  });

  it("keeps merge target key, database id, and expected version in the request", () => {
    const operations = buildOperations(proposedOperationsToFormValues([{
      operation: "add", feature_key: null, source_record_id: 1, reason: "新增", content: {
        module: "报表", feature_title: "导出", feature_description: "导出文件", acceptance_criteria: [],
      },
    }]), 11).operations!;
    expect(buildApprovalRequest("merge", "忽略", {
      id: 42, requirement_key: "REQ-0042", current_version_number: 7,
    }, operations, "合并")).toMatchObject({
      decision: "merge", title: null, target_requirement_key: "REQ-0042",
      expected_requirement_id: 42, expected_current_version: 7, operations,
    });
  });
});
